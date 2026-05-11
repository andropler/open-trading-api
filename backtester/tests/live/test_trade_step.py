from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import pytest

from kis_backtest.live.config.credentials import TelegramCreds
from kis_backtest.live.notify.telegram import TelegramClient
from kis_backtest.live.orchestrator.morning_routine import MorningRoutineResult
from kis_backtest.live.orchestrator.trade_step import (
    DryRunTradeStepResult,
    dry_run_trade_step,
)
from kis_backtest.live.regime.market_regime import RegimeFlags
from kis_backtest.live.signal.models import ExitProfile, Signal


def _routine_result(entries_allowed: bool) -> MorningRoutineResult:
    flags = RegimeFlags(
        m_bull_20_60=entries_allowed,
        m_no_1d_shock=entries_allowed,
        m_no_5d_drawdown=entries_allowed,
    )
    return MorningRoutineResult(
        asof_date=date(2026, 5, 5),
        market_symbol="069500",
        mode="vps",
        flags=flags,
        entries_allowed=entries_allowed,
        daily_rows=120,
        elapsed_seconds=0.05,
    )


def _profile() -> ExitProfile:
    return ExitProfile(
        stop_loss_pct=3.0,
        take_profit_pct=10.0,
        trail_activation_pct=5.0,
        trail_pct=4.0,
        max_hold_days=1,
    )


def _sig(variant: str, hhmm: int, ticker: str = "005930") -> Signal:
    hour, minute = divmod(hhmm, 100)
    ts = pd.Timestamp(f"2026-05-05 {hour:02d}:{minute:02d}:00")
    return Signal(
        source=variant.split("_")[0],
        variant=variant,
        ticker=ticker,
        entry_ts=ts,
        entry_price=70000.0,
        stop_price=67900.0,
        profile=_profile(),
        priority=5.0,
    )


@dataclass
class MockEngine:
    name: str
    signals: list[Signal] = field(default_factory=list)

    def candidate_signals(self, asof_date: pd.Timestamp) -> list[Signal]:
        return list(self.signals)


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


class TestEntryGated:
    def test_entries_disallowed_returns_empty_no_telegram(self, telegram):
        client, transport = telegram
        engine = MockEngine(name="m", signals=[_sig("reclaim_strict", 935)])
        result = dry_run_trade_step(_routine_result(False), [engine], client)
        assert isinstance(result, DryRunTradeStepResult)
        assert not result.entries_allowed
        assert result.selected_count == 0
        assert result.candidates_count == 0
        assert transport.calls == []


class TestEmptyEngines:
    def test_no_engines(self, telegram):
        client, transport = telegram
        result = dry_run_trade_step(_routine_result(True), [], client)
        assert result.entries_allowed
        assert result.selected_count == 0
        assert result.candidates_count == 0
        assert transport.calls == []


class TestNoSignals:
    def test_engines_return_empty(self, telegram):
        client, transport = telegram
        engine = MockEngine(name="m", signals=[])
        result = dry_run_trade_step(_routine_result(True), [engine], client)
        assert result.selected_count == 0
        assert transport.calls == []  # 0 신호일 때 텔레그램 noise 차단


class TestSignalAlert:
    def test_one_signal_sends_telegram(self, telegram):
        client, transport = telegram
        engine = MockEngine(name="m", signals=[_sig("reclaim_strict", 935)])
        result = dry_run_trade_step(_routine_result(True), [engine], client)
        assert result.selected_count == 1
        assert len(transport.calls) == 1
        text = transport.calls[0][1]["text"]
        assert "[SIGNAL][composite]" in text
        assert "1 candidates:" in text
        assert "005930 reclaim_strict" in text
        assert "entry=70000" in text
        assert "stop=67900" in text

    def test_multiple_signals_in_one_message(self, telegram):
        client, transport = telegram
        e1 = MockEngine(name="r", signals=[_sig("reclaim_strict", 935, ticker="005930")])
        e2 = MockEngine(
            name="o",
            signals=[_sig("orb_event_quality", 1015, ticker="000660")],
        )
        result = dry_run_trade_step(_routine_result(True), [e1, e2], client)
        assert result.selected_count == 2
        assert result.candidates_count == 2
        text = transport.calls[0][1]["text"]
        assert "2 candidates:" in text
        assert "005930" in text and "000660" in text


class TestVariantFiltering:
    def test_filtered_out_signals_not_alerted(self, telegram):
        client, transport = telegram
        # 950 hhmm 은 reclaim_strict allowed_hhmms 에 없음 → 필터링됨
        engine = MockEngine(name="r", signals=[_sig("reclaim_strict", 950)])
        result = dry_run_trade_step(_routine_result(True), [engine], client)
        assert result.candidates_count == 1
        assert result.selected_count == 0
        assert transport.calls == []  # 필터 후 0 → 알림 X


class TestTelegramOptional:
    def test_no_telegram(self):
        engine = MockEngine(name="m", signals=[_sig("reclaim_strict", 935)])
        result = dry_run_trade_step(_routine_result(True), [engine], None)
        assert result.selected_count == 1  # 텔레그램 없어도 결과는 정상

    def test_telegram_failure_graceful(self):
        transport = CapturingTransport(fail=True)
        creds = TelegramCreds(bot_token="bot:abc", chat_id="123")
        client = TelegramClient(creds=creds, transport=transport)
        engine = MockEngine(name="m", signals=[_sig("reclaim_strict", 935)])
        # 텔레그램 실패해도 routine 정상 종료
        result = dry_run_trade_step(_routine_result(True), [engine], client)
        assert result.selected_count == 1


class TestStrategyLabel:
    def test_label_propagates(self, telegram):
        client, transport = telegram
        engine = MockEngine(name="m", signals=[_sig("reclaim_strict", 935)])
        dry_run_trade_step(
            _routine_result(True), [engine], client, strategy_label="custom"
        )
        text = transport.calls[0][1]["text"]
        assert "[SIGNAL][custom]" in text
