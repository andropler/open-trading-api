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


def _trend(start: date, n: int, slope: float) -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="B")
    closes = [100.0 + i * slope for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1000] * n,
        }
    )


class FakeFetcher:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def fetch_daily(self, symbol, start_date, end_date) -> pd.DataFrame:
        return self.df.copy()


class FakeExecutor:
    def __init__(self, order_id: str = "ORD-1"):
        self.order_id = order_id
        self.calls = []

    def submit_order(self, symbol, side, quantity, order_type="market", price=0):
        self.calls.append((symbol, side, quantity, order_type, price))
        return self.order_id


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
    order_qty: int = 10
    side: str = "02"
    symbol: str = "005930"
    fill_qty: int = 10
    fill_price: int = 70000
    fill_time: str = "2026-05-07T09:35:00"
    is_fill: bool = True
    is_rejected: bool = False


class CapturingTransport:
    def __init__(self):
        self.calls = []

    def post(self, url, json):
        self.calls.append((url, json))
        return {"ok": True, "result": {}}


def _signal(variant="reclaim_strict", hhmm=935):
    h, m = divmod(hhmm, 100)
    return Signal(
        source="reclaim",
        variant=variant,
        ticker="005930",
        entry_ts=pd.Timestamp(f"2026-05-07 {h:02d}:{m:02d}:00"),
        entry_price=70000.0,
        stop_price=67900.0,
        profile=ExitProfile(
            stop_loss_pct=3.0,
            take_profit_pct=10.0,
            trail_activation_pct=5.0,
            trail_pct=4.0,
            max_hold_days=1,
        ),
        priority=5.0,
    )


@pytest.fixture
def trader_factory(tmp_path: Path):
    def make(*, df: pd.DataFrame, engines: list = None, telegram_client=None):
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
            fetcher=FakeFetcher(df),
            cache=cache,
            bar_buffer=buffer,
            aggregator=aggregator,
            executor=FakeExecutor(),
            tracker=tracker,
            killswitch=killswitch,
            ws_monitor=WsHealthMonitor(),
            api_monitor=Api5xxMonitor(),
            engines=engines or [],
            telegram=telegram_client,
        )

    return make


@pytest.fixture
def telegram() -> tuple[TelegramClient, CapturingTransport]:
    transport = CapturingTransport()
    creds = TelegramCreds(bot_token="bot:abc", chat_id="123")
    return TelegramClient(creds=creds, transport=transport), transport


class TestRunMorning:
    def test_uptrend_allows_entries(self, trader_factory, telegram):
        client, transport = telegram
        df = _trend(date(2026, 1, 1), 100, slope=0.5)
        trader = trader_factory(df=df, telegram_client=client)
        result = trader.run_morning(date(2026, 5, 30))
        assert result.entries_allowed
        assert result.mode == "vps"
        assert any("[STARTUP]" in c[1]["text"] for c in transport.calls)


class TestRunTradeDryRun:
    def test_dry_run_no_executor_call(self, trader_factory, telegram):
        client, transport = telegram
        df = _trend(date(2026, 1, 1), 100, slope=0.5)
        trader = trader_factory(
            df=df,
            engines=build_engines(MockEngine(name="r", signals=[_signal()])),
            telegram_client=client,
        )
        routine = trader.run_morning(date(2026, 5, 30))
        orders = trader.run_trade(routine, dry_run=True)
        assert len(orders) == 1
        assert not orders[0].submitted
        assert orders[0].reason == "dry_run"
        assert trader.executor.calls == []  # type: ignore


class TestRunTradeLive:
    def test_live_calls_executor(self, trader_factory, telegram):
        client, transport = telegram
        df = _trend(date(2026, 1, 1), 100, slope=0.5)
        trader = trader_factory(
            df=df,
            engines=build_engines(MockEngine(name="r", signals=[_signal()])),
            telegram_client=client,
        )
        routine = trader.run_morning(date(2026, 5, 30))
        orders = trader.run_trade(routine, dry_run=False)
        assert orders[0].submitted
        assert orders[0].order_id == "ORD-1"


class TestEmptyEngines:
    def test_no_engines_no_orders(self, trader_factory, telegram):
        client, _ = telegram
        df = _trend(date(2026, 1, 1), 100, slope=0.5)
        trader = trader_factory(df=df, engines=[], telegram_client=client)
        routine = trader.run_morning(date(2026, 5, 30))
        orders = trader.run_trade(routine, dry_run=True)
        assert orders == []


class TestOnPrice:
    def test_on_price_drives_aggregator(self, trader_factory):
        df = _trend(date(2026, 1, 1), 100, slope=0.5)
        trader = trader_factory(df=df)
        trader.on_price("005930", FakePrice("005930", "093001", 70000, 100))
        trader.on_price("005930", FakePrice("005930", "093530", 70200, 80))
        # 09:30 봉 flush 됨
        assert len(trader.bar_buffer.get("005930")) == 1


class TestOnFill:
    def test_on_fill_updates_tracker(self, trader_factory):
        df = _trend(date(2026, 1, 1), 100, slope=0.5)
        trader = trader_factory(df=df)
        notice = FakeFillNotice(side="02")
        trader.on_fill(notice)
        assert trader.tracker.get_position("005930") is not None


class TestShutdown:
    def test_shutdown_snapshots_and_alerts(self, trader_factory, telegram, tmp_path):
        client, transport = telegram
        df = _trend(date(2026, 1, 1), 100, slope=0.5)
        trader = trader_factory(df=df, telegram_client=client)
        trader.on_price("005930", FakePrice("005930", "093001", 70000, 100))
        trader.shutdown(date(2026, 5, 7))
        # snapshot 디렉토리 생성
        snap_dir = tmp_path / "snap" / "2026-05-07"
        assert snap_dir.exists()
        # DAILY 알림
        assert any("[DAILY]" in c[1]["text"] for c in transport.calls)
