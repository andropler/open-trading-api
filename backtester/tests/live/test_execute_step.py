from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from kis_backtest.live.config.credentials import TelegramCreds
from kis_backtest.live.notify.telegram import TelegramClient
from kis_backtest.live.orchestrator.execute_step import execute_step
from kis_backtest.live.orchestrator.trade_step import DryRunTradeStepResult
from kis_backtest.live.risk.killswitch import Killswitch
from kis_backtest.live.signal.models import ExitProfile, Signal


def _profile() -> ExitProfile:
    return ExitProfile(
        stop_loss_pct=3.0,
        take_profit_pct=10.0,
        trail_activation_pct=5.0,
        trail_pct=4.0,
        max_hold_days=1,
    )


def _sig(ticker: str = "005930", entry: float = 70000.0, priority: float = 5.0) -> Signal:
    return Signal(
        source="reclaim",
        variant="reclaim_strict",
        ticker=ticker,
        entry_ts=pd.Timestamp("2026-05-05 09:35:00"),
        entry_price=entry,
        stop_price=entry * 0.97,
        profile=_profile(),
        priority=priority,
    )


def _trade_result(
    entries_allowed: bool, signals: list[Signal] | None = None
) -> DryRunTradeStepResult:
    sigs = tuple(signals or [])
    return DryRunTradeStepResult(
        asof_date=date(2026, 5, 5),
        entries_allowed=entries_allowed,
        candidates_count=len(sigs),
        selected_count=len(sigs),
        selected=sigs,
    )


@pytest.fixture
def killswitch(tmp_path: Path) -> Killswitch:
    return Killswitch(
        halt_flag_path=tmp_path / "HALT.flag",
        archive_dir=tmp_path / "halts",
        capital_krw=5_000_000,
    )


class FakeExecutor:
    def __init__(self, raise_exc: Exception | None = None, order_id: str = "ORD123"):
        self.raise_exc = raise_exc
        self.order_id = order_id
        self.calls: list[tuple[str, str, int, str, int]] = []

    def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str = "market",
        price: int = 0,
    ) -> str:
        self.calls.append((symbol, side, quantity, order_type, price))
        if self.raise_exc:
            raise self.raise_exc
        return self.order_id


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
def telegram() -> tuple[TelegramClient, CapturingTransport]:
    transport = CapturingTransport()
    creds = TelegramCreds(bot_token="bot:abc", chat_id="123")
    return TelegramClient(creds=creds, transport=transport), transport


class TestEntryGate:
    def test_disallowed_returns_empty(self, killswitch, telegram):
        client, transport = telegram
        executor = FakeExecutor()
        result = execute_step(
            _trade_result(False), executor, killswitch, 5_000_000, client
        )
        assert result == []
        assert executor.calls == []
        assert transport.calls == []

    def test_zero_signals_returns_empty(self, killswitch, telegram):
        client, transport = telegram
        executor = FakeExecutor()
        result = execute_step(
            _trade_result(True, []), executor, killswitch, 5_000_000, client
        )
        assert result == []
        assert executor.calls == []


class TestKillswitchGate:
    def test_halted_skips_with_warn_alert(self, killswitch, telegram):
        client, transport = telegram
        # 강제 halt
        killswitch.halt_flag_path.write_text(
            '{"condition_id":"daily_loss","value":-3.5,"threshold":-3.0,"ts":"x"}'
        )
        executor = FakeExecutor()
        result = execute_step(
            _trade_result(True, [_sig()]), executor, killswitch, 5_000_000, client
        )
        assert result == []
        assert executor.calls == []
        assert len(transport.calls) == 1
        assert "[WARN]" in transport.calls[0][1]["text"]
        assert "HALTed" in transport.calls[0][1]["text"]


class TestSizing:
    def test_insufficient_capital(self, killswitch, telegram):
        client, transport = telegram
        executor = FakeExecutor()
        # entry 70000 인데 capital 50000 → qty 0
        result = execute_step(
            _trade_result(True, [_sig(entry=70000)]),
            executor,
            killswitch,
            50_000,
            client,
        )
        assert len(result) == 1
        assert not result[0].submitted
        assert result[0].reason == "insufficient_capital"
        assert executor.calls == []

    def test_sizing_division(self, killswitch, telegram):
        client, transport = telegram
        executor = FakeExecutor()
        # capital 5000000, entry 70000 → qty = 5000000 / 70000 = 71.4 → 71
        result = execute_step(
            _trade_result(True, [_sig(entry=70000)]),
            executor,
            killswitch,
            5_000_000,
            client,
            dry_run=False,
        )
        assert result[0].submitted
        assert executor.calls[0][2] == 71  # qty


class TestDryRun:
    def test_dry_run_does_not_submit(self, killswitch, telegram):
        client, transport = telegram
        executor = FakeExecutor()
        result = execute_step(
            _trade_result(True, [_sig()]),
            executor,
            killswitch,
            5_000_000,
            client,
            dry_run=True,
        )
        assert len(result) == 1
        assert not result[0].submitted
        assert result[0].reason == "dry_run"
        assert executor.calls == []  # 실제 호출 X
        text = transport.calls[0][1]["text"]
        assert "DRY-RUN BUY" in text
        assert "005930" in text

    def test_dry_run_multi_signals_max_positions_1(self, killswitch, telegram):
        client, transport = telegram
        executor = FakeExecutor()
        result = execute_step(
            _trade_result(True, [_sig(ticker="005930"), _sig(ticker="000660")]),
            executor,
            killswitch,
            5_000_000,
            client,
            dry_run=True,
            max_positions=1,
        )
        # max_positions=1 이므로 첫 번째만 처리
        assert len(result) == 1
        assert result[0].request.ticker == "005930"

    def test_max_positions_2(self, killswitch, telegram):
        client, transport = telegram
        executor = FakeExecutor()
        result = execute_step(
            _trade_result(True, [_sig(ticker="005930"), _sig(ticker="000660")]),
            executor,
            killswitch,
            10_000_000,
            client,
            dry_run=True,
            max_positions=2,
        )
        assert len(result) == 2


class TestLiveSubmit:
    def test_live_success_returns_order_id(self, killswitch, telegram):
        client, transport = telegram
        executor = FakeExecutor(order_id="ORD-9999")
        result = execute_step(
            _trade_result(True, [_sig()]),
            executor,
            killswitch,
            5_000_000,
            client,
            dry_run=False,
        )
        assert result[0].submitted
        assert result[0].order_id == "ORD-9999"
        text = transport.calls[0][1]["text"]
        assert "[ORDER]" in text
        assert "order_id=ORD-9999" in text

    def test_live_failure_records_error(self, killswitch, telegram):
        client, transport = telegram
        executor = FakeExecutor(raise_exc=RuntimeError("KIS 5xx"))
        result = execute_step(
            _trade_result(True, [_sig()]),
            executor,
            killswitch,
            5_000_000,
            client,
            dry_run=False,
        )
        assert not result[0].submitted
        assert "KIS 5xx" in result[0].error
        text = transport.calls[0][1]["text"]
        assert "[ERROR]" in text


class TestNoTelegram:
    def test_telegram_none_does_not_break(self, killswitch):
        executor = FakeExecutor()
        result = execute_step(
            _trade_result(True, [_sig()]),
            executor,
            killswitch,
            5_000_000,
            None,
            dry_run=True,
        )
        assert len(result) == 1


class TestStrategyLabel:
    def test_label_propagates(self, killswitch, telegram):
        client, transport = telegram
        executor = FakeExecutor()
        execute_step(
            _trade_result(True, [_sig()]),
            executor,
            killswitch,
            5_000_000,
            client,
            dry_run=True,
            strategy_label="alt",
        )
        assert "[ORDER][alt]" in transport.calls[0][1]["text"]


class TestInvalidParams:
    def test_zero_capital(self, killswitch):
        executor = FakeExecutor()
        with pytest.raises(ValueError, match="capital_krw"):
            execute_step(
                _trade_result(True, [_sig()]), executor, killswitch, 0, None
            )

    def test_zero_max_positions(self, killswitch):
        executor = FakeExecutor()
        with pytest.raises(ValueError, match="max_positions"):
            execute_step(
                _trade_result(True, [_sig()]),
                executor,
                killswitch,
                5_000_000,
                None,
                max_positions=0,
            )
