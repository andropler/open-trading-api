from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

from kis_backtest.live.data.fetcher import refresh_market_index
from kis_backtest.live.data.cache import DailyOHLCVCache
from kis_backtest.live.data.kis_fetcher import KISDailyFetcher
from kis_backtest.models import Bar, Resolution


class FakeProvider:
    def __init__(self, bars: list[Bar]):
        self.bars = bars
        self.calls: list[tuple[str, date, date, Resolution]] = []

    def get_history(
        self,
        symbol: str,
        start: date,
        end: date,
        resolution: Resolution = Resolution.DAILY,
    ) -> Iterable[Bar]:
        self.calls.append((symbol, start, end, resolution))
        return list(self.bars)


def _bars(start: date, n: int) -> list[Bar]:
    return [
        Bar(
            time=datetime.combine(start + timedelta(days=i), datetime.min.time()),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1_000 + i,
        )
        for i in range(n)
    ]


class TestFetchDaily:
    def test_basic_conversion(self):
        provider = FakeProvider(_bars(date(2026, 1, 1), 5))
        fetcher = KISDailyFetcher(provider)
        df = fetcher.fetch_daily("069500", date(2026, 1, 1), date(2026, 1, 5))
        assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
        assert len(df) == 5
        # date 컬럼이 date 타입(datetime이 아닌)인지 확인
        assert df["date"].iloc[0] == date(2026, 1, 1)

    def test_passes_resolution_daily(self):
        provider = FakeProvider(_bars(date(2026, 1, 1), 1))
        fetcher = KISDailyFetcher(provider)
        fetcher.fetch_daily("069500", date(2026, 1, 1), date(2026, 1, 5))
        _, _, _, resolution = provider.calls[0]
        assert resolution == Resolution.DAILY

    def test_empty_response(self):
        provider = FakeProvider([])
        fetcher = KISDailyFetcher(provider)
        df = fetcher.fetch_daily("069500", date(2026, 1, 1), date(2026, 1, 5))
        assert df.empty
        assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]

    def test_volume_int_type(self):
        provider = FakeProvider(_bars(date(2026, 1, 1), 3))
        fetcher = KISDailyFetcher(provider)
        df = fetcher.fetch_daily("069500", date(2026, 1, 1), date(2026, 1, 3))
        assert df["volume"].dtype.kind in ("i", "u")  # integer

    def test_ohlc_float_type(self):
        provider = FakeProvider(_bars(date(2026, 1, 1), 3))
        fetcher = KISDailyFetcher(provider)
        df = fetcher.fetch_daily("069500", date(2026, 1, 1), date(2026, 1, 3))
        for col in ("open", "high", "low", "close"):
            assert df[col].dtype.kind == "f"


class TestIntegrationWithRefresh:
    def test_kis_fetcher_drives_refresh(self, tmp_path):
        # 80일치 일봉 → KISDailyFetcher → refresh_market_index → cache 채워짐
        provider = FakeProvider(_bars(date(2026, 1, 1), 80))
        fetcher = KISDailyFetcher(provider)
        cache = DailyOHLCVCache(tmp_path / "daily")
        out = refresh_market_index(cache, fetcher, "069500", date(2026, 5, 30))
        assert len(out) == 80
        assert cache.last_date("069500") is not None
