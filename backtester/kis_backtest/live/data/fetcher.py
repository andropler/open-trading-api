"""일봉 fetcher Protocol + refresh_market_index 워커.

전략은 매일 한 번 시장지수(예: 069500) 일봉을 갱신해 regime.compute_flags 의
입력으로 사용한다. fetcher 가 history_days 분량을 가져오면 캐시는 union 으로
업데이트된다 (date 중복 시 새 값 우선).
"""

from __future__ import annotations

from datetime import date as _date
from datetime import timedelta
from typing import Protocol

import pandas as pd

from kis_backtest.live.data.cache import DailyOHLCVCache


class DailyBarFetcher(Protocol):
    def fetch_daily(
        self, symbol: str, start_date: _date, end_date: _date
    ) -> pd.DataFrame: ...


def refresh_market_index(
    cache: DailyOHLCVCache,
    fetcher: DailyBarFetcher,
    symbol: str,
    asof_date: _date,
    history_days: int = 120,
) -> pd.DataFrame:
    """시장지수 일봉을 fetch + 캐시 union 후 전체 캐시 내용을 반환.

    history_days 는 SMA60 계산을 위해 60 이상이어야 한다. 빈 응답은 정상이
    아니므로 RuntimeError 발생. asof_date 가 fetcher 가 줄 수 있는 최신보다
    미래일 경우 fetcher 가 응답 가능한 최신까지만 채운다.
    """
    if history_days < 60:
        raise ValueError(f"history_days must be >= 60, got {history_days}")
    start = asof_date - timedelta(days=history_days)
    fresh = fetcher.fetch_daily(symbol, start, asof_date)
    if fresh is None or fresh.empty:
        raise RuntimeError(
            f"fetcher returned empty data for {symbol} ({start} ~ {asof_date})"
        )
    existing = cache.read(symbol)
    if existing is None or existing.empty:
        merged = fresh
    else:
        # union — 같은 date 는 fresh 가 우선 (drop_duplicates keep="last")
        combined = pd.concat([existing, fresh], ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"]).dt.normalize()
        merged = (
            combined.drop_duplicates(subset="date", keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )
    cache.write(symbol, merged)
    result = cache.read(symbol)
    if result is None:
        raise RuntimeError(
            f"cache read returned None after write for {symbol} — filesystem anomaly"
        )
    return result
