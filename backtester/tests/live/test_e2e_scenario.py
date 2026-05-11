"""End-to-end 통합 시나리오 — morning → trade → on_price → on_fill → shutdown."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from kis_backtest.live.config.credentials import (
    KISCreds,
    LiveConfig,
    TelegramCreds,
    TradingLimits,
)
from kis_backtest.live.data.bar_aggregator import FiveMinuteBarAggregator
from kis_backtest.live.data.bar_buffer import FiveMinuteBarBuffer
from kis_backtest.live.data.cache import DailyOHLCVCache
from kis_backtest.live.notify.telegram import TelegramClient
from kis_backtest.live.orchestrator.live_trader import LiveTrader, build_engines
from kis_backtest.live.orchestrator.monitors import Api5xxMonitor, WsHealthMonitor
from kis_backtest.live.position.tracker import PositionTracker
from kis_backtest.live.risk.killswitch import Killswitch
from kis_backtest.live.signal.models import ExitProfile, Signal


def _trend(slope: float) -> pd.DataFrame:
    dates = pd.date_range(date(2026, 1, 1), periods=100, freq="B")
    closes = [100.0 + i * slope for i in range(100)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1_000] * 100,
        }
    )


class FakeFetcher:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def fetch_daily(self, symbol, start_date, end_date):
        return self.df.copy()


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def submit_order(self, symbol, side, quantity, order_type="market", price=0):
        self.calls.append((symbol, side, quantity))
        return f"ORD-{len(self.calls)}"


@dataclass
class MockEngine:
    name: str
    signals: list = field(default_factory=list)

    def candidate_signals(self, asof_date):
        return list(self.signals)


@dataclass
class FakePrice:
    symbol: str
    time: str
    price: int
    volume: int


@dataclass
class FakeFillNotice:
    customer_id: str = "C1"
    account_no: str = "12345-01"
    order_no: str = "O-1"
    order_qty: int = 71
    side: str = "02"
    symbol: str = "005930"
    fill_qty: int = 71
    fill_price: int = 70_000
    fill_time: str = "2026-05-07T09:35:00"
    is_fill: bool = True
    is_rejected: bool = False


class CapturingTransport:
    def __init__(self):
        self.calls = []

    def post(self, url, json):
        self.calls.append(json)
        return {"ok": True, "result": {}}


def _signal() -> Signal:
    return Signal(
        source="reclaim",
        variant="reclaim_strict",
        ticker="005930",
        entry_ts=pd.Timestamp("2026-05-07 09:35:00"),
        entry_price=70_000.0,
        stop_price=67_900.0,
        profile=ExitProfile(
            stop_loss_pct=3.0,
            take_profit_pct=10.0,
            trail_activation_pct=5.0,
            trail_pct=4.0,
            max_hold_days=1,
        ),
        priority=5.0,
    )


def _make_trader(tmp_path: Path, *, slope: float, signals: list, telegram_client):
    cache = DailyOHLCVCache(tmp_path / "daily")
    buffer = FiveMinuteBarBuffer(snapshot_dir=tmp_path / "snap")
    aggregator = FiveMinuteBarAggregator(buffer=buffer, today=date(2026, 5, 7))
    tracker = PositionTracker(tmp_path / "positions.json")
    killswitch = Killswitch(
        halt_flag_path=tmp_path / "HALT.flag",
        archive_dir=tmp_path / "halts",
        capital_krw=5_000_000,
    )
    config = LiveConfig(
        mode="vps",
        telegram=TelegramCreds(bot_token="bot:abc", chat_id="123"),
        kis=KISCreds(appkey="ak", appsecret="as", account_no="acc", mode="vps"),
        limits=TradingLimits(
            capital_krw=5_000_000,
            daily_loss_pct=3.0,
            cumulative_loss_pct=8.0,
        ),
    )
    return LiveTrader(
        config=config,
        fetcher=FakeFetcher(_trend(slope)),
        cache=cache,
        bar_buffer=buffer,
        aggregator=aggregator,
        executor=FakeExecutor(),
        tracker=tracker,
        killswitch=killswitch,
        ws_monitor=WsHealthMonitor(),
        api_monitor=Api5xxMonitor(),
        engines=build_engines(MockEngine(name="r", signals=signals)),
        telegram=telegram_client,
    )


@pytest.fixture
def telegram() -> tuple[TelegramClient, CapturingTransport]:
    transport = CapturingTransport()
    creds = TelegramCreds(bot_token="bot:abc", chat_id="123")
    return TelegramClient(creds=creds, transport=transport), transport


class TestProfitableScenario:
    def test_full_flow_with_profit(self, tmp_path, telegram):
        client, transport = telegram
        trader = _make_trader(
            tmp_path, slope=0.5, signals=[_signal()], telegram_client=client
        )
        # 1) morning
        routine = trader.run_morning(date(2026, 5, 7))
        assert routine.entries_allowed
        # 2) trade live (FakeExecutor)
        orders = trader.run_trade(routine, dry_run=False)
        assert len(orders) == 1 and orders[0].submitted
        # 3) on_price
        trader.on_price("005930", FakePrice("005930", "093001", 70_000, 100))
        trader.on_price("005930", FakePrice("005930", "093530", 70_200, 50))
        assert len(trader.bar_buffer.get("005930")) == 1
        # 4) on_fill — 매수 체결
        trader.on_fill(FakeFillNotice(side="02"))
        assert trader.tracker.get_position("005930") is not None
        # 5) on_fill — 매도 체결 (수익)
        trader.on_fill(
            FakeFillNotice(
                side="01",
                fill_qty=71,
                fill_price=71_000,
                fill_time="2026-05-07T10:30:00",
                order_no="O-2",
            )
        )
        assert trader.tracker.get_position("005930") is None
        assert trader.tracker.state.realized_pnl_krw > 0
        assert not trader.killswitch.is_halted()
        # 6) shutdown
        trader.shutdown(date(2026, 5, 7))
        # 카테고리 순서 검증
        cats = [
            c["text"][1 : c["text"].index("]")]
            for c in transport.calls
        ]
        assert "STARTUP" in cats
        assert "SIGNAL" in cats
        assert "ORDER" in cats
        assert "EXIT" in cats
        assert "DAILY" in cats


class TestKillswitchScenario:
    def test_loss_triggers_halt(self, tmp_path, telegram):
        client, transport = telegram
        trader = _make_trader(
            tmp_path, slope=0.5, signals=[_signal()], telegram_client=client
        )
        routine = trader.run_morning(date(2026, 5, 7))
        trader.run_trade(routine, dry_run=False)
        trader.on_fill(FakeFillNotice(side="02", fill_qty=100, fill_price=70_000))
        # 큰 손실 매도 → -3% 초과
        trader.on_fill(
            FakeFillNotice(
                side="01",
                fill_qty=100,
                fill_price=68_000,
                fill_time="2026-05-07T10:30:00",
                order_no="O-2",
            )
        )
        assert trader.killswitch.is_halted()
        cats = [c["text"][1 : c["text"].index("]")] for c in transport.calls]
        assert "HALT" in cats


class TestEntryBlockedScenario:
    def test_bear_market_no_entry(self, tmp_path, telegram):
        client, transport = telegram
        trader = _make_trader(
            tmp_path, slope=-0.5, signals=[_signal()], telegram_client=client
        )
        routine = trader.run_morning(date(2026, 5, 7))
        assert not routine.entries_allowed
        orders = trader.run_trade(routine, dry_run=True)
        assert orders == []
        # SIGNAL 알림 없음 (entries_allowed=False)
        cats = [c["text"][1 : c["text"].index("]")] for c in transport.calls]
        assert "SIGNAL" not in cats
