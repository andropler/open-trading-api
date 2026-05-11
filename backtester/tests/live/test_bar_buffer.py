from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from kis_backtest.live.data.bar_buffer import FiveMinuteBarBuffer


def _bar(t: datetime, base: float = 100.0) -> dict:
    return {
        "time": t,
        "open": base,
        "high": base + 0.5,
        "low": base - 0.5,
        "close": base + 0.2,
        "volume": 1_000,
    }


class TestAppendAndGet:
    def test_append_then_get_sorted(self):
        buf = FiveMinuteBarBuffer()
        buf.append("005930", _bar(datetime(2026, 5, 5, 10, 0)))
        buf.append("005930", _bar(datetime(2026, 5, 5, 9, 35), base=99.0))
        df = buf.get("005930")
        assert len(df) == 2
        assert df["time"].is_monotonic_increasing

    def test_get_unknown_symbol_empty_df(self):
        buf = FiveMinuteBarBuffer()
        df = buf.get("999999")
        assert df.empty
        assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]

    def test_append_missing_keys_rejected(self):
        buf = FiveMinuteBarBuffer()
        bad = {"time": datetime(2026, 5, 5, 9, 35), "open": 100, "close": 101}
        with pytest.raises(ValueError, match="missing keys"):
            buf.append("005930", bad)


class TestSnapshot:
    def test_snapshot_writes_parquet_per_symbol(self, tmp_path: Path):
        buf = FiveMinuteBarBuffer(snapshot_dir=tmp_path / "snapshots")
        buf.append("005930", _bar(datetime(2026, 5, 5, 9, 35)))
        buf.append("000660", _bar(datetime(2026, 5, 5, 9, 35), base=200.0))
        buf.snapshot(date(2026, 5, 5))
        date_dir = tmp_path / "snapshots" / "2026-05-05"
        assert (date_dir / "005930_5m.parquet").exists()
        assert (date_dir / "000660_5m.parquet").exists()

    def test_snapshot_no_dir_is_noop(self):
        buf = FiveMinuteBarBuffer(snapshot_dir=None)
        buf.append("005930", _bar(datetime(2026, 5, 5, 9, 35)))
        result = buf.snapshot(date(2026, 5, 5))
        assert result is None

    def test_snapshot_skips_empty_symbols(self, tmp_path: Path):
        buf = FiveMinuteBarBuffer(snapshot_dir=tmp_path / "snapshots")
        # 빈 상태에서 snapshot — date dir 만 생성, 파일 없음
        result = buf.snapshot(date(2026, 5, 5))
        assert result is not None
        assert result.exists()
        assert list(result.glob("*.parquet")) == []

    def test_snapshot_roundtrip(self, tmp_path: Path):
        buf = FiveMinuteBarBuffer(snapshot_dir=tmp_path / "snapshots")
        buf.append("005930", _bar(datetime(2026, 5, 5, 9, 35), base=100.0))
        buf.append("005930", _bar(datetime(2026, 5, 5, 9, 40), base=101.0))
        buf.snapshot(date(2026, 5, 5))
        path = tmp_path / "snapshots" / "2026-05-05" / "005930_5m.parquet"
        df = pd.read_parquet(path)
        assert len(df) == 2
        assert df["time"].is_monotonic_increasing


class TestClear:
    def test_clear_empties_memory(self):
        buf = FiveMinuteBarBuffer()
        buf.append("005930", _bar(datetime(2026, 5, 5, 9, 35)))
        assert buf.symbols() == ["005930"]
        buf.clear()
        assert buf.symbols() == []
        assert buf.get("005930").empty
