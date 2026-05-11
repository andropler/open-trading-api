from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from kis_backtest.live.config.credentials import TelegramCreds
from kis_backtest.live.notify.telegram import TelegramClient
from kis_backtest.live.orchestrator.fill_handler import handle_fill
from kis_backtest.live.position.tracker import PositionTracker
from kis_backtest.live.risk.killswitch import Killswitch


@dataclass
class FakeFillNotice:
    customer_id: str = "C1"
    account_no: str = "12345-01"
    order_no: str = "ORD-1"
    order_qty: int = 10
    side: str = "02"  # 매수
    symbol: str = "005930"
    fill_qty: int = 10
    fill_price: int = 70000
    fill_time: str = "2026-05-06T09:35:00"
    is_fill: bool = True
    is_rejected: bool = False


class CapturingTransport:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict) -> dict:
        self.calls.append((url, json))
        if self.fail:
            raise RuntimeError("net down")
        return {"ok": True, "result": {}}


@pytest.fixture
def tracker(tmp_path: Path) -> PositionTracker:
    return PositionTracker(tmp_path / "positions.json")


@pytest.fixture
def killswitch(tmp_path: Path) -> Killswitch:
    return Killswitch(
        halt_flag_path=tmp_path / "HALT.flag",
        archive_dir=tmp_path / "halts",
        capital_krw=5_000_000,
    )


@pytest.fixture
def telegram() -> tuple[TelegramClient, CapturingTransport]:
    transport = CapturingTransport()
    creds = TelegramCreds(bot_token="bot:abc", chat_id="123")
    return TelegramClient(creds=creds, transport=transport), transport


class TestBuyFill:
    def test_buy_fill_opens_position_and_alerts(self, tracker, killswitch, telegram):
        client, transport = telegram
        notice = FakeFillNotice(side="02", symbol="005930", fill_qty=10, fill_price=70000)
        halt = handle_fill(notice, tracker, killswitch, client)
        assert halt is None
        pos = tracker.get_position("005930")
        assert pos is not None
        assert pos.qty == 10
        assert pos.avg_price == 70000.0
        text = transport.calls[0][1]["text"]
        assert "[ORDER]" in text
        assert "FILL BUY 005930" in text


class TestSellFill:
    def test_sell_fill_closes_and_emits_exit_alert(self, tracker, killswitch, telegram):
        client, transport = telegram
        # 먼저 매수
        tracker.open_position("005930", 10, 70000, "2026-05-06T09:35:00")
        # 매도 체결
        sell = FakeFillNotice(
            side="01",
            symbol="005930",
            fill_qty=10,
            fill_price=71000,
            order_no="ORD-2",
            fill_time="2026-05-06T10:30:00",
        )
        handle_fill(sell, tracker, killswitch, client)
        assert tracker.get_position("005930") is None
        assert tracker.state.realized_pnl_krw > 0
        text = transport.calls[0][1]["text"]
        assert "[EXIT]" in text
        assert "FILL SELL 005930" in text
        assert "+" in text  # 수익 부호


class TestRejected:
    def test_rejected_emits_error_no_tracker_change(self, tracker, killswitch, telegram):
        client, transport = telegram
        notice = FakeFillNotice(is_rejected=True, order_no="ORD-X")
        halt = handle_fill(notice, tracker, killswitch, client)
        assert halt is None
        assert tracker.get_position("005930") is None  # 변화 없음
        text = transport.calls[0][1]["text"]
        assert "[ERROR]" in text
        assert "rejected" in text


class TestAcknowledged:
    def test_ack_only_no_fill_no_tracker_change(self, tracker, killswitch, telegram):
        client, transport = telegram
        notice = FakeFillNotice(is_fill=False)
        handle_fill(notice, tracker, killswitch, client)
        assert tracker.get_position("005930") is None
        text = transport.calls[0][1]["text"]
        assert "[ORDER]" in text
        assert "ACK" in text


class TestUnknownSide:
    def test_invalid_side_raises(self, tracker, killswitch, telegram):
        client, transport = telegram
        notice = FakeFillNotice(side="99")
        with pytest.raises(ValueError, match="side"):
            handle_fill(notice, tracker, killswitch, client)


class TestKillswitchTriggered:
    def test_loss_close_triggers_halt(self, tracker, killswitch, telegram):
        client, transport = telegram
        # 큰 손실로 daily_loss -3% 초과 유도 (capital 5M 기준 -150,001원 이상)
        tracker.open_position("005930", 100, 70000, "2026-05-06T09:35:00")
        # 매수: 100주 × 70,000 = 7,000,000 평단
        # 매도 단가 68,000 → gross = (68000-70000)*100 = -200,000
        # 비용 ~ commission+tax ≈ 15,000+12,240 ≈ 27,240
        # net ≈ -227,240 → -4.5% (5M 자본)
        sell = FakeFillNotice(
            side="01",
            symbol="005930",
            fill_qty=100,
            fill_price=68000,
            fill_time="2026-05-06T10:30:00",
        )
        halt = handle_fill(sell, tracker, killswitch, client)
        assert halt is not None
        assert halt.condition_id == "daily_loss"
        assert killswitch.is_halted()
        # HALT 알림이 telegram 에도 발생
        halt_alerts = [c for c in transport.calls if "[HALT]" in c[1]["text"]]
        assert len(halt_alerts) == 1


class TestNoTelegram:
    def test_telegram_none_does_not_break(self, tracker, killswitch):
        notice = FakeFillNotice()
        handle_fill(notice, tracker, killswitch, None)
        assert tracker.get_position("005930") is not None


class TestPartialFills:
    def test_open_then_partial_close(self, tracker, killswitch, telegram):
        client, transport = telegram
        # 1차 매수 5주
        buy1 = FakeFillNotice(
            side="02", fill_qty=5, fill_price=70000, order_no="O-1"
        )
        handle_fill(buy1, tracker, killswitch, client)
        # 2차 매수 5주 (가중평균)
        buy2 = FakeFillNotice(
            side="02", fill_qty=5, fill_price=72000, order_no="O-2"
        )
        handle_fill(buy2, tracker, killswitch, client)
        pos = tracker.get_position("005930")
        assert pos.qty == 10
        assert pos.avg_price == 71000.0  # (70000*5 + 72000*5)/10
        # 부분 매도 4주
        sell1 = FakeFillNotice(
            side="01", fill_qty=4, fill_price=73000, order_no="O-3"
        )
        handle_fill(sell1, tracker, killswitch, client)
        pos = tracker.get_position("005930")
        assert pos is not None
        assert pos.qty == 6


class TestTelegramFailure:
    def test_telegram_failure_graceful(self, tracker, killswitch):
        transport = CapturingTransport(fail=True)
        creds = TelegramCreds(bot_token="bot:abc", chat_id="123")
        client = TelegramClient(creds=creds, transport=transport)
        notice = FakeFillNotice()
        # 텔레그램 실패해도 tracker 갱신은 정상
        handle_fill(notice, tracker, killswitch, client)
        assert tracker.get_position("005930") is not None
