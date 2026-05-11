"""run_trading_day main loop 테스트.

시간 가속을 위해 sleep_func/now_func 주입. mock clock 으로 09:00 → 5m boundary
→ 15:35 흐름을 빠르게 시뮬레이션.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
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
from kis_backtest.live.orchestrator.live_trader import LiveTrader
from kis_backtest.live.orchestrator.monitors import Api5xxMonitor, WsHealthMonitor
from kis_backtest.live.orchestrator.trading_day import (
    _next_5m_boundary,
    run_trading_day,
)
from kis_backtest.live.position.tracker import PositionTracker
from kis_backtest.live.risk.killswitch import Killswitch
from kis_backtest.live.signal.reclaim_engine import LiveReclaimEngine


def _trend_daily(asof: date, n: int = 100, slope: float = 0.5) -> pd.DataFrame:
    dates = pd.date_range(asof - timedelta(days=n + 30), periods=n, freq="B")
    closes = [100.0 + i * slope for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1_000] * n,
        }
    )


class FakeFetcher:
    def __init__(self, df_map: dict[str, pd.DataFrame] | None = None, default: pd.DataFrame | None = None):
        self.df_map = df_map or {}
        self.default = default
        self.calls: list[str] = []

    def fetch_daily(self, symbol, start_date, end_date) -> pd.DataFrame:
        self.calls.append(symbol)
        if symbol in self.df_map:
            return self.df_map[symbol].copy()
        if self.default is not None:
            return self.default.copy()
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume"]
        )


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def submit_order(self, symbol, side, quantity, order_type="market", price=0):
        self.calls.append((symbol, side, quantity))
        return f"ORD-{len(self.calls)}"


class MockClock:
    def __init__(self, start: datetime):
        self.now = start
        self.sleeps: list[float] = []

    def time_now(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
def trader(tmp_path: Path) -> LiveTrader:
    asof = date(2026, 5, 11)
    cache = DailyOHLCVCache(tmp_path / "daily")
    buffer = FiveMinuteBarBuffer()
    aggregator = FiveMinuteBarAggregator(buffer=buffer, today=asof)
    tracker = PositionTracker(tmp_path / "positions.json")
    killswitch = Killswitch(
        halt_flag_path=tmp_path / "HALT.flag",
        archive_dir=tmp_path / "halts",
        capital_krw=5_000_000,
    )
    config = LiveConfig(
        mode="vps",
        telegram=TelegramCreds(bot_token="bot:abc", chat_id="0"),
        kis=KISCreds(appkey="ak", appsecret="as", account_no="acc", mode="vps"),
        limits=TradingLimits(
            capital_krw=5_000_000, daily_loss_pct=3.0, cumulative_loss_pct=8.0
        ),
    )
    return LiveTrader(
        config=config,
        fetcher=FakeFetcher(default=_trend_daily(asof)),
        cache=cache,
        bar_buffer=buffer,
        aggregator=aggregator,
        executor=FakeExecutor(),
        tracker=tracker,
        killswitch=killswitch,
        ws_monitor=WsHealthMonitor(),
        api_monitor=Api5xxMonitor(),
        engines=[],
        telegram=None,
    )


class TestBoundary:
    def test_mid_minute_rounds_up(self):
        assert _next_5m_boundary(datetime(2026, 5, 11, 9, 32, 15)) == datetime(
            2026, 5, 11, 9, 35, 0
        )

    def test_exact_boundary_advances(self):
        assert _next_5m_boundary(datetime(2026, 5, 11, 9, 30, 0)) == datetime(
            2026, 5, 11, 9, 35, 0
        )

    def test_after_boundary_rounds_up_next(self):
        assert _next_5m_boundary(datetime(2026, 5, 11, 9, 30, 1)) == datetime(
            2026, 5, 11, 9, 35, 0
        )


class TestPreLoopOnly:
    def test_enable_loop_false_returns_after_setup(self, trader):
        asof = date(2026, 5, 11)
        engine = LiveReclaimEngine()
        fetcher = FakeFetcher(default=_trend_daily(asof))
        result = run_trading_day(
            trader,
            engine,
            fetcher,
            asof_date=asof,
            universe=["005930", "000660"],
            enable_loop=False,
        )
        assert result.entries_allowed
        assert result.eval_cycles == 0
        assert engine in trader.engines
        assert sorted(fetcher.calls) == ["000660", "005930"]
        assert result.universe == ("005930", "000660")


class TestEntriesBlocked:
    def test_bear_market_skips_eval_loop(self, trader, tmp_path):
        asof = date(2026, 5, 11)
        # bear: 강한 하락
        bear = _trend_daily(asof, slope=-0.5)
        trader.fetcher = FakeFetcher(default=bear)
        engine = LiveReclaimEngine()
        fetcher = FakeFetcher(default=bear)
        clock = MockClock(datetime.combine(asof, time(15, 34, 30)))
        result = run_trading_day(
            trader,
            engine,
            fetcher,
            asof_date=asof,
            universe=["005930"],
            now_func=clock.time_now,
            sleep_func=clock.sleep,
        )
        assert not result.entries_allowed
        assert result.eval_cycles == 0


class TestEvalLoop:
    def test_loop_runs_until_shutdown(self, trader):
        asof = date(2026, 5, 11)
        engine = LiveReclaimEngine()
        fetcher = FakeFetcher(default=_trend_daily(asof))
        # 15:20 시작 → 5m boundary 2회(15:20+offset, 15:25+offset) + shutdown
        clock = MockClock(datetime.combine(asof, time(15, 20, 0)))
        result = run_trading_day(
            trader,
            engine,
            fetcher,
            asof_date=asof,
            universe=["005930"],
            now_func=clock.time_now,
            sleep_func=clock.sleep,
            dry_run=True,
        )
        assert result.entries_allowed
        # eval_cycles 가 최소 1회 이상 (15:20+offset → 15:25 이전 평가)
        assert result.eval_cycles >= 1
        # buffer 가 비어있어 engine 이 빈 signal 반환 — orders_submitted 0
        assert result.orders_submitted == 0

    def test_loop_breaks_on_killswitch(self, trader):
        asof = date(2026, 5, 11)
        # halt 강제 발화
        trader.killswitch.halt_flag_path.write_text(
            '{"condition_id":"daily_loss","value":-3.5,"threshold":-3.0,"ts":"x"}'
        )
        engine = LiveReclaimEngine()
        fetcher = FakeFetcher(default=_trend_daily(asof))
        clock = MockClock(datetime.combine(asof, time(15, 20, 0)))
        result = run_trading_day(
            trader,
            engine,
            fetcher,
            asof_date=asof,
            universe=["005930"],
            now_func=clock.time_now,
            sleep_func=clock.sleep,
        )
        assert result.halt_triggered


class TestUniverseFetch:
    def test_fetcher_called_per_symbol(self, trader):
        asof = date(2026, 5, 11)
        engine = LiveReclaimEngine()
        fetcher = FakeFetcher(default=_trend_daily(asof))
        run_trading_day(
            trader,
            engine,
            fetcher,
            asof_date=asof,
            universe=["005930", "000660", "373220"],
            enable_loop=False,
        )
        assert sorted(fetcher.calls) == ["000660", "005930", "373220"]

    def test_fetch_failure_does_not_abort(self, trader):
        asof = date(2026, 5, 11)
        class FlakyFetcher:
            def __init__(self):
                self.calls = []
            def fetch_daily(self, symbol, start, end):
                self.calls.append(symbol)
                if symbol == "BAD":
                    raise RuntimeError("KIS 5xx")
                return _trend_daily(asof)
        fetcher = FlakyFetcher()
        engine = LiveReclaimEngine()
        result = run_trading_day(
            trader,
            engine,
            fetcher,
            asof_date=asof,
            universe=["005930", "BAD", "000660"],
            enable_loop=False,
        )
        # BAD 는 빠지지만 나머지 2개는 정상 진행
        assert result.entries_allowed
        assert "BAD" in fetcher.calls
