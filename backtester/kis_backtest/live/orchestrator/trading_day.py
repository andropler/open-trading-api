"""TradingDay 매일 1회 실행되는 장중 main loop.

흐름:
1. morning_routine 호출 (시장 레짐 평가 + STARTUP 알림)
2. universe 일봉 fetch → LiveReclaimEngine.set_data
3. 장 시작(09:00) 까지 대기 → 5m boundary 마다 신호 평가 + 주문
4. shutdown 시각(15:35)에 trader.shutdown 호출 → DAILY 알림

WebSocket(price + fill) 구독은 본 함수 시작 전 외부에서 별도 thread/프로세스로
연결해야 한다 (KIS WS 는 asyncio.run blocking). buffer/tracker/killswitch 는
LiveTrader 가 공유 보관.

테스트성: sleep_func, now_func, hhmm_func 주입으로 가속 가능.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime, time, timedelta
from typing import Callable, Iterable

import pandas as pd

from kis_backtest.live.data.fetcher import DailyBarFetcher
from kis_backtest.live.notify.telegram import Category
from kis_backtest.live.orchestrator.live_trader import LiveTrader
from kis_backtest.live.signal.reclaim_engine import LiveReclaimEngine

logger = logging.getLogger(__name__)


def _hhmm(t: time) -> int:
    return t.hour * 100 + t.minute


def _next_5m_boundary(now: datetime) -> datetime:
    """now 이후의 다음 5m 봉 마감 시각 (분 단위 floor)."""
    floored = now.replace(second=0, microsecond=0)
    minute_mod = floored.minute % 5
    if minute_mod == 0 and now.second == 0 and now.microsecond == 0:
        # 정확히 5m boundary 이면 다음 boundary 반환
        return floored + timedelta(minutes=5)
    return floored + timedelta(minutes=5 - minute_mod)


@dataclass
class TradingDayResult:
    asof_date: _date
    entries_allowed: bool
    eval_cycles: int = 0
    signals_seen: int = 0
    orders_submitted: int = 0
    halt_triggered: bool = False
    universe: tuple[str, ...] = field(default_factory=tuple)


def run_trading_day(
    trader: LiveTrader,
    reclaim_engine: LiveReclaimEngine,
    daily_fetcher: DailyBarFetcher,
    *,
    asof_date: _date,
    universe: Iterable[str],
    history_days: int = 60,
    market_open: time = time(9, 0),
    market_close: time = time(15, 30),
    shutdown_at: time = time(15, 35),
    eval_offset_seconds: int = 5,
    dry_run: bool = True,
    sleep_func: Callable[[float], None] | None = None,
    now_func: Callable[[], datetime] | None = None,
    enable_loop: bool = True,
) -> TradingDayResult:
    """장중 매 5m boundary 마다 신호 평가 + 주문 발행 + shutdown.

    enable_loop=False 면 morning_routine + universe 일봉 fetch + engine 초기화까지만
    수행하고 즉시 반환 (테스트 / 사전점검용).
    """
    import time as _time

    sleep_func = sleep_func or _time.sleep
    now_func = now_func or datetime.now

    universe_tuple = tuple(universe)

    # 1. morning_routine
    routine = trader.run_morning(asof_date)
    result = TradingDayResult(
        asof_date=asof_date,
        entries_allowed=routine.entries_allowed,
        universe=universe_tuple,
    )

    # 2. universe 일봉 fetch
    start = asof_date - timedelta(days=history_days * 2)  # 영업일이 아닌 달력일 여유
    daily_data: dict[str, pd.DataFrame] = {}
    for sym in universe_tuple:
        try:
            df = daily_fetcher.fetch_daily(sym, start, asof_date)
        except Exception as e:
            logger.error("fetch_daily failed for %s: %s", sym, e)
            continue
        if df is not None and not df.empty:
            daily_data[sym] = df
    logger.info(
        "universe daily fetched: %d/%d symbols", len(daily_data), len(universe_tuple)
    )

    # 3. reclaim engine 초기화
    intraday_init: dict[str, pd.DataFrame] = {sym: pd.DataFrame() for sym in universe_tuple}
    reclaim_engine.set_data(daily_data, intraday_init)
    if reclaim_engine not in trader.engines:
        trader.engines.append(reclaim_engine)

    if not enable_loop:
        return result

    if not routine.entries_allowed:
        # 진입 불가 — buffer 유지하며 shutdown 까지 대기
        logger.info("entries not allowed; idle until shutdown")
        _idle_until(now_func, sleep_func, asof_date, shutdown_at)
        trader.shutdown(asof_date)
        return result

    # 4. 장중 5m boundary loop
    while True:
        now = now_func()
        if now.time() >= shutdown_at:
            break
        if now.time() < market_open:
            # 장 시작까지 대기
            target = datetime.combine(asof_date, market_open)
            sleep_func(max(1.0, (target - now).total_seconds()))
            continue

        boundary = _next_5m_boundary(now)
        wake = boundary + timedelta(seconds=eval_offset_seconds)
        sleep_seconds = max(0.0, (wake - now).total_seconds())
        if sleep_seconds > 0:
            sleep_func(sleep_seconds)

        now = now_func()
        if now.time() >= market_close:
            # 장 마감 후 추가 평가 없음
            break

        # buffer → intraday (universe 만)
        intraday = {sym: trader.bar_buffer.get(sym) for sym in universe_tuple}
        # 빈 buffer 라도 set_data 호출 (캐시 invalidate)
        reclaim_engine.set_data(daily_data, intraday)

        # 신호 평가 + 주문
        trade = None
        orders = []
        try:
            trade = _from_route_to_trade(trader, routine)
            orders = trader.run_trade(routine, dry_run=dry_run)
        except Exception as e:
            logger.error("eval cycle failed: %s", e)

        result.eval_cycles += 1
        if trade is not None:
            result.signals_seen += trade.selected_count
        for o in orders:
            if o.submitted:
                result.orders_submitted += 1

        if trader.killswitch.is_halted():
            result.halt_triggered = True
            _safe_alert(
                trader,
                Category.WARN,
                "killswitch HALTed mid-day; exiting eval loop",
            )
            break

    # 5. shutdown
    _idle_until(now_func, sleep_func, asof_date, shutdown_at)
    trader.shutdown(asof_date)
    return result


def _from_route_to_trade(trader: LiveTrader, routine):
    """run_trade 와 동일 흐름을 재현하면서 trade_result 반환을 보존."""
    from kis_backtest.live.orchestrator.trade_step import dry_run_trade_step

    return dry_run_trade_step(
        routine,
        trader.engines,
        trader.telegram,
        strategy_label=trader.strategy_label,
    )


def _idle_until(
    now_func: Callable[[], datetime],
    sleep_func: Callable[[float], None],
    asof_date: _date,
    target: time,
) -> None:
    while True:
        now = now_func()
        if now.time() >= target or now.date() > asof_date:
            return
        target_dt = datetime.combine(asof_date, target)
        remaining = (target_dt - now).total_seconds()
        if remaining <= 0:
            return
        sleep_func(min(remaining, 30.0))


def _safe_alert(trader: LiveTrader, category: Category, body: str) -> None:
    if trader.telegram is None:
        return
    try:
        trader.telegram.send(category, body, strategy=trader.strategy_label)
    except Exception as e:
        logger.error("telegram %s alert failed: %s", category.value, e)


__all__ = [
    "TradingDayResult",
    "run_trading_day",
]
