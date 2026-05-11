from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from kis_backtest.live.data.cache import DailyOHLCVCache
from kis_backtest.live.data.fetcher import refresh_market_index
from kis_backtest.live.regime.market_regime import compute_flags


def _trend_bars(start_date: date, n: int, slope: float, base: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range(start_date, periods=n, freq="B")
    closes = [base + i * slope for i in range(n)]
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
    def __init__(self, response: pd.DataFrame):
        self.response = response

    def fetch_daily(self, symbol, start_date, end_date) -> pd.DataFrame:
        return self.response.copy()


@pytest.fixture
def cache(tmp_path: Path) -> DailyOHLCVCache:
    return DailyOHLCVCache(tmp_path / "daily")


class TestE2EBull:
    def test_strong_uptrend_passes_bull(self, cache):
        bars = _trend_bars(date(2026, 1, 1), 100, slope=0.5)
        fetcher = FakeFetcher(bars)
        df = refresh_market_index(cache, fetcher, "069500", date(2026, 5, 30))
        asof = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=1)
        flags = compute_flags(df, asof)
        assert flags.m_bull_20_60
        assert flags.passes_base_gate()


class TestE2EBear:
    def test_downtrend_fails_bull(self, cache):
        bars = _trend_bars(date(2026, 1, 1), 100, slope=-0.5, base=200.0)
        fetcher = FakeFetcher(bars)
        df = refresh_market_index(cache, fetcher, "069500", date(2026, 5, 30))
        asof = pd.Timestamp(df["date"].iloc[-1]) + pd.Timedelta(days=1)
        flags = compute_flags(df, asof)
        assert not flags.m_bull_20_60


class TestE2ECacheReuse:
    def test_second_refresh_yields_same_flags(self, cache):
        bars = _trend_bars(date(2026, 1, 1), 100, slope=0.5)
        fetcher = FakeFetcher(bars)
        df1 = refresh_market_index(cache, fetcher, "069500", date(2026, 5, 30))
        df2 = refresh_market_index(cache, fetcher, "069500", date(2026, 5, 30))
        asof = pd.Timestamp(df1["date"].iloc[-1]) + pd.Timedelta(days=1)
        f1 = compute_flags(df1, asof)
        f2 = compute_flags(df2, asof)
        assert f1 == f2


class TestE2EColdStart:
    def test_cache_starts_empty_and_populates(self, cache):
        assert cache.last_date("069500") is None
        bars = _trend_bars(date(2026, 1, 1), 80, slope=0.5)
        fetcher = FakeFetcher(bars)
        refresh_market_index(cache, fetcher, "069500", date(2026, 4, 30))
        assert cache.last_date("069500") is not None
