from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pytest

from kis_backtest.live.data.bar_aggregator import FiveMinuteBarAggregator, floor_5m
from kis_backtest.live.data.bar_buffer import FiveMinuteBarBuffer


@dataclass
class FakePrice:
    symbol: str
    time: str
    price: int
    volume: int


class TestFloor5m:
    def test_basic(self):
        assert floor_5m(date(2026, 5, 7), "093712") == datetime(2026, 5, 7, 9, 35)

    def test_boundary(self):
        assert floor_5m(date(2026, 5, 7), "093000") == datetime(2026, 5, 7, 9, 30)

    def test_invalid_short(self):
        with pytest.raises(ValueError, match="6-digit"):
            floor_5m(date(2026, 5, 7), "9300")

    def test_invalid_non_digit(self):
        with pytest.raises(ValueError, match="6-digit"):
            floor_5m(date(2026, 5, 7), "abcdef")


class TestSingleBarAccumulation:
    def test_three_ticks_within_one_bar(self, tmp_path):
        buf = FiveMinuteBarBuffer()
        agg = FiveMinuteBarAggregator(buffer=buf, today=date(2026, 5, 7))
        agg.on_price("005930", FakePrice("005930", "093001", 70000, 100))
        agg.on_price("005930", FakePrice("005930", "093215", 70500, 50))
        agg.on_price("005930", FakePrice("005930", "093430", 69800, 200))
        # 아직 봉 경계 안 넘었으므로 buffer 비어있음
        assert buf.get("005930").empty
        agg.flush_all()
        df = buf.get("005930")
        assert len(df) == 1
        row = df.iloc[0]
        assert row["open"] == 70000
        assert row["high"] == 70500
        assert row["low"] == 69800
        assert row["close"] == 69800
        assert row["volume"] == 350


class TestBoundaryFlush:
    def test_cross_5m_boundary_flushes_previous(self):
        buf = FiveMinuteBarBuffer()
        agg = FiveMinuteBarAggregator(buffer=buf, today=date(2026, 5, 7))
        agg.on_price("005930", FakePrice("005930", "093001", 70000, 100))
        agg.on_price("005930", FakePrice("005930", "093530", 70200, 80))  # 다음 봉
        df = buf.get("005930")
        assert len(df) == 1  # 이전 09:30 봉이 flush
        assert df.iloc[0]["close"] == 70000
        assert df.iloc[0]["time"] == datetime(2026, 5, 7, 9, 30)
        agg.flush_all()
        df = buf.get("005930")
        assert len(df) == 2
        assert df.iloc[1]["time"] == datetime(2026, 5, 7, 9, 35)


class TestMultiSymbol:
    def test_independent_state_per_symbol(self):
        buf = FiveMinuteBarBuffer()
        agg = FiveMinuteBarAggregator(buffer=buf, today=date(2026, 5, 7))
        agg.on_price("005930", FakePrice("005930", "093001", 70000, 10))
        agg.on_price("000660", FakePrice("000660", "093002", 100000, 20))
        agg.on_price("005930", FakePrice("005930", "093530", 70500, 5))
        # 005930 09:30 봉만 flush, 000660 진행중
        assert len(buf.get("005930")) == 1
        assert buf.get("000660").empty
        agg.flush_all()
        assert len(buf.get("005930")) == 2
        assert len(buf.get("000660")) == 1


class TestSetToday:
    def test_set_today_flushes_and_updates(self):
        buf = FiveMinuteBarBuffer()
        agg = FiveMinuteBarAggregator(buffer=buf, today=date(2026, 5, 7))
        agg.on_price("005930", FakePrice("005930", "093001", 70000, 10))
        agg.set_today(date(2026, 5, 8))
        # set_today 가 flush_all 호출 → 5/7 봉이 buffer 에 들어감
        df = buf.get("005930")
        assert len(df) == 1
        assert df.iloc[0]["time"] == datetime(2026, 5, 7, 9, 30)
        # 새 today 로 봉 시작
        agg.on_price("005930", FakePrice("005930", "100015", 71000, 5))
        agg.flush_all()
        df = buf.get("005930")
        assert df.iloc[1]["time"] == datetime(2026, 5, 8, 10, 0)


class TestFlushAllEmpty:
    def test_flush_all_no_state_noop(self):
        buf = FiveMinuteBarBuffer()
        agg = FiveMinuteBarAggregator(buffer=buf, today=date(2026, 5, 7))
        agg.flush_all()
        assert buf.symbols() == []
