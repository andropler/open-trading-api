from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kis_backtest.live.data.cache import DailyOHLCVCache


def _make_df(start: str, n: int) -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1000 * (i + 1) for i in range(n)],
        }
    )


@pytest.fixture
def cache(tmp_path: Path) -> DailyOHLCVCache:
    return DailyOHLCVCache(tmp_path / "daily")


class TestRoundtrip:
    def test_write_then_read(self, cache):
        df = _make_df("2026-01-05", 30)
        cache.write("069500", df)
        loaded = cache.read("069500")
        assert loaded is not None
        assert len(loaded) == 30
        assert list(loaded.columns) == ["date", "open", "high", "low", "close", "volume"]

    def test_read_missing_returns_none(self, cache):
        assert cache.read("999999") is None

    def test_path_format(self, cache):
        assert cache.path("069500").name == "069500_daily.parquet"


class TestLastDate:
    def test_last_date_after_write(self, cache):
        df = _make_df("2026-01-05", 5)
        cache.write("069500", df)
        last = cache.last_date("069500")
        assert last == pd.Timestamp(df["date"].iloc[-1]).date()

    def test_last_date_no_cache(self, cache):
        assert cache.last_date("999999") is None


class TestSorting:
    def test_unsorted_input_persisted_sorted(self, cache):
        df = _make_df("2026-01-05", 5)
        df = df.iloc[::-1].reset_index(drop=True)  # 역순
        cache.write("069500", df)
        loaded = cache.read("069500")
        assert loaded["date"].is_monotonic_increasing


class TestValidation:
    def test_empty_df_rejected(self, cache):
        empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        with pytest.raises(ValueError, match="empty"):
            cache.write("069500", empty)

    def test_missing_columns_rejected(self, cache):
        bad = pd.DataFrame({"date": pd.date_range("2026-01-05", periods=3, freq="B"), "close": [1, 2, 3]})
        with pytest.raises(ValueError, match="missing columns"):
            cache.write("069500", bad)

    def test_extra_columns_dropped(self, cache):
        df = _make_df("2026-01-05", 3)
        df["extra"] = [1, 2, 3]
        cache.write("069500", df)
        loaded = cache.read("069500")
        assert "extra" not in loaded.columns


class TestAtomicWrite:
    def test_overwrite_existing(self, cache):
        df1 = _make_df("2026-01-05", 5)
        cache.write("069500", df1)
        df2 = _make_df("2026-02-01", 10)
        cache.write("069500", df2)
        loaded = cache.read("069500")
        assert len(loaded) == 10
        assert pd.Timestamp(loaded["date"].iloc[0]).strftime("%Y-%m-%d") == "2026-02-02"


class TestCorruption:
    def test_corrupt_file_raises_and_backs_up(self, cache):
        target = cache.path("069500")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"not a parquet file")
        with pytest.raises(RuntimeError, match="corrupted"):
            cache.read("069500")
        backups = list(target.parent.glob("069500_daily.corrupt-*.parquet"))
        assert len(backups) == 1
