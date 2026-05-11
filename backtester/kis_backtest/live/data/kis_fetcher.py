"""KISDailyFetcher: KISDataProvider → DailyBarFetcher Protocol 어댑터.

기존 providers/kis/data.py 의 get_history(symbol, start, end, resolution=DAILY)
를 wrap 하여 DataFrame[date, open, high, low, close, volume] 으로 변환한다.

KISDataProvider 자체에 의존하지 않도록 호환 Protocol 시그니처(get_history)
만 받으면 동작 — 단위 테스트는 mock provider 로 진행.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from typing import Iterable, Protocol

import pandas as pd

from kis_backtest.models import Bar, Resolution


class _HistoryProvider(Protocol):
    def get_history(
        self,
        symbol: str,
        start: _date,
        end: _date,
        resolution: Resolution = Resolution.DAILY,
    ) -> Iterable[Bar]: ...


class KISDailyFetcher:
    def __init__(self, provider: _HistoryProvider):
        self._provider = provider

    def fetch_daily(
        self, symbol: str, start_date: _date, end_date: _date
    ) -> pd.DataFrame:
        bars = list(
            self._provider.get_history(
                symbol, start_date, end_date, Resolution.DAILY
            )
        )
        columns = ["date", "open", "high", "low", "close", "volume"]
        if not bars:
            return pd.DataFrame(columns=columns)
        rows = [
            {
                # Bar.time 은 Pydantic 으로 datetime 강제. 분기는 forward-compat 방어용.
                "date": (b.time.date() if isinstance(b.time, datetime) else b.time),
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": int(b.volume),
            }
            for b in bars
        ]
        return pd.DataFrame(rows, columns=columns)
