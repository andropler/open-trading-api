from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from kis_backtest.live.data.cache import DailyOHLCVCache
from kis_backtest.live.data.fetcher import refresh_market_index


def _bars(start: date, n: int, base: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(
        {
            "date": dates,
            "open": [base + i for i in range(n)],
            "high": [base + 1 + i for i in range(n)],
            "low": [base - 1 + i for i in range(n)],
            "close": [base + 0.5 + i for i in range(n)],
            "volume": [1_000 * (i + 1) for i in range(n)],
        }
    )


class FakeFetcher:
    def __init__(self, response: pd.DataFrame):
        self.response = response
        self.calls: list[tuple[str, date, date]] = []

    def fetch_daily(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        self.calls.append((symbol, start_date, end_date))
        return self.response.copy()


@pytest.fixture
def cache(tmp_path: Path) -> DailyOHLCVCache:
    return DailyOHLCVCache(tmp_path / "daily")


class TestFirstFetch:
    def test_no_cache_writes_full_response(self, cache):
        bars = _bars(date(2026, 1, 1), 80)
        fetcher = FakeFetcher(bars)
        out = refresh_market_index(cache, fetcher, "069500", date(2026, 4, 30))
        assert len(out) == 80
        assert len(fetcher.calls) == 1


class TestUnion:
    def test_existing_plus_fresh_overlap(self, cache):
        # 캐시에 1~50 일자 / fetcher 가 25~75 일자 → 1~75 union
        existing = _bars(date(2026, 1, 1), 50)
        cache.write("069500", existing)
        fresh = _bars(date(2026, 1, 1) + timedelta(days=24 * 365 // 252), 50, base=200.0)
        # 단순화: fresh 가 마지막 50일치 (overlap 25일 + 신규 25일)
        # 위 산식보다 직접 만들기
        fresh = existing.iloc[25:].copy()
        fresh["close"] = fresh["close"] + 1000.0  # 새 값 우선 검증용
        appended = _bars(date(2026, 4, 1), 25, base=300.0)
        fresh = pd.concat([fresh, appended], ignore_index=True)
        fetcher = FakeFetcher(fresh)
        out = refresh_market_index(cache, fetcher, "069500", date(2026, 4, 30))
        # union 결과: 50 + 25 신규 = 75 + 중복 제거
        unique_dates = pd.to_datetime(out["date"]).dt.normalize().nunique()
        assert len(out) == unique_dates
        # overlap 영역의 close 가 fresh 값(+1000)으로 갱신됐는지 확인
        merged_sample = out[out["date"] == existing["date"].iloc[26]]["close"].iloc[0]
        original_sample = existing.loc[26, "close"]
        assert merged_sample > original_sample + 999


class TestIdempotence:
    def test_repeated_refresh_same_result(self, cache):
        bars = _bars(date(2026, 1, 1), 80)
        fetcher = FakeFetcher(bars)
        out1 = refresh_market_index(cache, fetcher, "069500", date(2026, 4, 30))
        out2 = refresh_market_index(cache, fetcher, "069500", date(2026, 4, 30))
        assert len(out1) == len(out2)
        pd.testing.assert_frame_equal(
            out1.reset_index(drop=True), out2.reset_index(drop=True)
        )


class TestErrors:
    def test_empty_response_raises(self, cache):
        empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        fetcher = FakeFetcher(empty)
        with pytest.raises(RuntimeError, match="empty"):
            refresh_market_index(cache, fetcher, "069500", date(2026, 4, 30))

    def test_history_days_below_60_rejected(self, cache):
        fetcher = FakeFetcher(_bars(date(2026, 1, 1), 30))
        with pytest.raises(ValueError, match="history_days"):
            refresh_market_index(
                cache, fetcher, "069500", date(2026, 4, 30), history_days=30
            )


class TestParameters:
    def test_history_days_passed_to_fetcher(self, cache):
        bars = _bars(date(2026, 1, 1), 80)
        fetcher = FakeFetcher(bars)
        asof = date(2026, 4, 30)
        refresh_market_index(cache, fetcher, "069500", asof, history_days=120)
        symbol, start, end = fetcher.calls[0]
        assert symbol == "069500"
        assert end == asof
        assert (asof - start).days == 120


class TestFutureAsof:
    def test_future_asof_proceeds(self, cache):
        # asof_date 가 fetcher 응답 최신보다 미래여도 fetcher 응답을 그대로 사용
        bars = _bars(date(2026, 1, 1), 80)  # 마지막 영업일 ~ 2026-04-22
        fetcher = FakeFetcher(bars)
        future_asof = date(2030, 1, 1)
        out = refresh_market_index(cache, fetcher, "069500", future_asof)
        # fetcher 가 80행 응답 → 캐시에 그대로 보존
        assert len(out) == 80
        # last 가 미래보다 과거여도 정상 진행
        assert cache.last_date("069500") < future_asof
