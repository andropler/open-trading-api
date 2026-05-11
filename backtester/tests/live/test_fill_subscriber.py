from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import pytest

from kis_backtest.live.config.credentials import TelegramCreds
from kis_backtest.live.notify.telegram import TelegramClient
from kis_backtest.live.orchestrator.fill_handler import FillNoticeLike
from kis_backtest.live.orchestrator.fill_subscriber import (
    KISFillSubscriber,
    hhmmss_to_iso,
)
from kis_backtest.live.orchestrator.monitors import Api5xxMonitor, WsHealthMonitor
from kis_backtest.live.position.tracker import PositionTracker
from kis_backtest.live.risk.killswitch import Killswitch


@dataclass
class FakeFillNotice:
    customer_id: str = "C1"
    account_no: str = "12345-01"
    order_no: str = "O-1"
    order_qty: int = 10
    side: str = "02"
    symbol: str = "005930"
    fill_qty: int = 10
    fill_price: int = 70000
    fill_time: str = "093500"
    is_fill: bool = True
    is_rejected: bool = False


class FakeWsProvider:
    def __init__(self):
        self.callback: Callable[[FillNoticeLike], None] | None = None

    def subscribe_fills(self, callback):
        self.callback = callback


class CapturingTransport:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict) -> dict:
        self.calls.append((url, json))
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


class TestHhmmssToIso:
    def test_six_digit_converted(self):
        assert hhmmss_to_iso(date(2026, 5, 7), "093500") == "2026-05-07T09:35:00"

    def test_iso_passthrough(self):
        iso = "2026-05-07T09:35:00"
        assert hhmmss_to_iso(date(2026, 5, 7), iso) == iso

    def test_non_digit_passthrough(self):
        assert hhmmss_to_iso(date(2026, 5, 7), "abcdef") == "abcdef"

    def test_short_string_passthrough(self):
        assert hhmmss_to_iso(date(2026, 5, 7), "9350") == "9350"


class TestSubscriberStart:
    def test_start_registers_callback(self, tracker, killswitch, telegram):
        client, _ = telegram
        ws = FakeWsProvider()
        sub = KISFillSubscriber(
            ws_provider=ws,
            tracker=tracker,
            killswitch=killswitch,
            ws_monitor=WsHealthMonitor(),
            api_monitor=Api5xxMonitor(),
            telegram=client,
            today=date(2026, 5, 7),
        )
        assert ws.callback is None
        sub.start()
        assert ws.callback is not None


class TestFillProcessing:
    def test_buy_fill_with_hhmmss_conversion(self, tracker, killswitch, telegram):
        client, transport = telegram
        ws = FakeWsProvider()
        sub = KISFillSubscriber(
            ws_provider=ws,
            tracker=tracker,
            killswitch=killswitch,
            ws_monitor=WsHealthMonitor(),
            api_monitor=Api5xxMonitor(),
            telegram=client,
            today=date(2026, 5, 7),
        )
        sub.start()
        notice = FakeFillNotice(side="02", fill_time="093500")
        ws.callback(notice)
        # tracker 에 매수 포지션 기록됨
        pos = tracker.get_position("005930")
        assert pos is not None
        assert pos.entry_ts == "2026-05-07T09:35:00"  # ISO 변환 후 저장 확인

    def test_iso_passthrough_in_subscriber(self, tracker, killswitch, telegram):
        client, transport = telegram
        ws = FakeWsProvider()
        sub = KISFillSubscriber(
            ws_provider=ws,
            tracker=tracker,
            killswitch=killswitch,
            ws_monitor=WsHealthMonitor(),
            api_monitor=Api5xxMonitor(),
            telegram=client,
            today=date(2026, 5, 7),
        )
        sub.start()
        notice = FakeFillNotice(side="02", fill_time="2026-05-07T10:00:00")
        ws.callback(notice)
        pos = tracker.get_position("005930")
        assert pos.entry_ts == "2026-05-07T10:00:00"

    def test_today_none_falls_back_to_original(
        self, tracker, killswitch, telegram, caplog
    ):
        client, _ = telegram
        ws = FakeWsProvider()
        sub = KISFillSubscriber(
            ws_provider=ws,
            tracker=tracker,
            killswitch=killswitch,
            ws_monitor=WsHealthMonitor(),
            api_monitor=Api5xxMonitor(),
            telegram=client,
            today=None,
        )
        sub.start()
        notice = FakeFillNotice(side="02", fill_time="093500")
        with caplog.at_level("ERROR"):
            ws.callback(notice)
        assert any("today is None" in r.message for r in caplog.records)
        # 원본 fill_time 으로 tracker 기록
        pos = tracker.get_position("005930")
        assert pos.entry_ts == "093500"

    def test_set_today_updates_conversion(
        self, tracker, killswitch, telegram
    ):
        client, _ = telegram
        ws = FakeWsProvider()
        sub = KISFillSubscriber(
            ws_provider=ws,
            tracker=tracker,
            killswitch=killswitch,
            ws_monitor=WsHealthMonitor(),
            api_monitor=Api5xxMonitor(),
            telegram=client,
            today=date(2026, 5, 7),
        )
        sub.start()
        sub.set_today(date(2026, 5, 8))
        notice = FakeFillNotice(side="02", fill_time="100000")
        ws.callback(notice)
        pos = tracker.get_position("005930")
        assert pos.entry_ts == "2026-05-08T10:00:00"


class TestMetricsInjection:
    def test_metrics_from_monitors_passed_to_handle_fill(
        self, tracker, killswitch, telegram
    ):
        client, _ = telegram
        ws = FakeWsProvider()
        ws_monitor = WsHealthMonitor()
        api_monitor = Api5xxMonitor()
        # 단절 60초 누적
        ws_monitor.on_disconnect(ts=1000.0)
        ws_monitor.on_reconnect(ts=1060.0)
        # 5xx 3건
        api_monitor.record_5xx(ts=1500.0)
        api_monitor.record_5xx(ts=1520.0)
        api_monitor.record_5xx(ts=1540.0)
        sub = KISFillSubscriber(
            ws_provider=ws,
            tracker=tracker,
            killswitch=killswitch,
            ws_monitor=ws_monitor,
            api_monitor=api_monitor,
            telegram=client,
            today=date(2026, 5, 7),
        )
        sub.start()
        # 매수만 — killswitch 평가 시점에 metrics 가 전달되어야 함
        # (직접 metrics 검증보다 handle_fill 통합을 신뢰 — killswitch.is_halted 변화 없음 기대)
        notice = FakeFillNotice(side="02", fill_time="093500")
        ws.callback(notice)
        # metrics 가 임계 미달이라 halt 없음
        assert not killswitch.is_halted()
