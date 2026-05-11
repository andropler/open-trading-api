"""실시간 체결가(HHMMSS, price, volume) → 5m OHLCV 봉 집계.

KIS H0STCNT0 의 RealtimePrice 가 들어올 때마다 on_price 호출. 5m 봉 경계를
넘으면 이전 봉을 FiveMinuteBarBuffer 에 flush. 장 마감 시 flush_all 로 잔여
봉 dump.

KIS time 필드는 HHMMSS 6자리 (예: '093712'). today 와 결합해 ISO datetime 으로
floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime, time
from typing import Protocol

from kis_backtest.live.data.bar_buffer import FiveMinuteBarBuffer


class RealtimePriceLike(Protocol):
    symbol: str
    time: str  # HHMMSS
    price: int
    volume: int


@dataclass
class _BarState:
    bar_start: datetime
    open: int
    high: int
    low: int
    close: int
    volume: int


def floor_5m(today: _date, hhmmss: str) -> datetime:
    if len(hhmmss) != 6 or not hhmmss.isdigit():
        raise ValueError(f"hhmmss must be 6-digit numeric, got {hhmmss!r}")
    h = int(hhmmss[:2])
    m = int(hhmmss[2:4])
    floored_minute = (m // 5) * 5
    return datetime.combine(today, time(h, floored_minute))


@dataclass
class FiveMinuteBarAggregator:
    buffer: FiveMinuteBarBuffer
    today: _date
    _states: dict[str, _BarState] = field(default_factory=dict, init=False, repr=False)

    def set_today(self, today: _date) -> None:
        # 자정 롤오버: 진행 중 봉을 모두 이전 today 기준으로 flush 후 갱신
        self.flush_all()
        self.today = today

    def on_price(self, symbol: str, price: RealtimePriceLike) -> None:
        bar_start = floor_5m(self.today, price.time)
        prev = self._states.get(symbol)
        if prev is not None and prev.bar_start != bar_start:
            self._flush(symbol)
            prev = None
        if prev is None:
            self._states[symbol] = _BarState(
                bar_start=bar_start,
                open=price.price,
                high=price.price,
                low=price.price,
                close=price.price,
                volume=price.volume,
            )
        else:
            prev.high = max(prev.high, price.price)
            prev.low = min(prev.low, price.price)
            prev.close = price.price
            prev.volume += price.volume

    def flush_all(self) -> None:
        for symbol in list(self._states.keys()):
            self._flush(symbol)

    def _flush(self, symbol: str) -> None:
        state = self._states.pop(symbol, None)
        if state is None:
            return
        self.buffer.append(
            symbol,
            {
                "time": state.bar_start,
                "open": state.open,
                "high": state.high,
                "low": state.low,
                "close": state.close,
                "volume": state.volume,
            },
        )
