"""매일 아침 routine: 시장지수 갱신 + 레짐 평가 + 텔레그램 알림.

운영자(또는 스케줄러)가 매일 장 시작 직전에 호출. 시장지수 일봉을 갱신하고
m_bull_20_60 등 레짐 플래그를 계산해 진입 허용 여부를 결정한 뒤 텔레그램
STARTUP 메시지로 보고한다. fetcher 가 RuntimeError 면 ERROR 알림 후 re-raise.
텔레그램 자체가 죽으면 콘솔 로그만 남기고 routine 은 계속 진행.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date as _date

from kis_backtest.live.data.cache import DailyOHLCVCache
from kis_backtest.live.data.fetcher import DailyBarFetcher, refresh_market_index
from kis_backtest.live.notify.telegram import Category, TelegramClient
from kis_backtest.live.regime.market_regime import RegimeFlags, compute_flags

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MorningRoutineResult:
    asof_date: _date
    market_symbol: str
    mode: str
    flags: RegimeFlags
    entries_allowed: bool
    daily_rows: int
    elapsed_seconds: float


def _safe_telegram_send(
    telegram: TelegramClient | None, category: Category, body: str, strategy: str
) -> None:
    if telegram is None:
        return
    try:
        telegram.send(category, body, strategy=strategy)
    except Exception as e:
        logger.error("telegram send failed (%s): %s", category.value, e)


def morning_routine(
    fetcher: DailyBarFetcher,
    cache: DailyOHLCVCache,
    telegram: TelegramClient | None,
    market_symbol: str,
    asof_date: _date,
    *,
    mode: str = "unknown",
    history_days: int = 120,
    strategy_label: str = "composite",
) -> MorningRoutineResult:
    # elapsed_seconds 는 데이터 준비(refresh + compute_flags) 시간만 측정.
    # telegram 송신 시간은 포함하지 않음 (송신 장애가 측정값을 오염시키지 않게).
    started = time.perf_counter()
    try:
        df = refresh_market_index(
            cache, fetcher, market_symbol, asof_date, history_days=history_days
        )
    except Exception as e:
        _safe_telegram_send(
            telegram,
            Category.ERROR,
            f"refresh_market_index failed mode={mode} symbol={market_symbol} asof={asof_date}: {e}",
            strategy_label,
        )
        raise
    flags = compute_flags(df, asof_date)
    entries_allowed = flags.passes_base_gate()
    elapsed = time.perf_counter() - started
    body = (
        f"mode={mode} symbol={market_symbol} asof={asof_date} rows={len(df)} "
        f"bull_20_60={flags.m_bull_20_60} no_shock={flags.m_no_1d_shock} "
        f"no_dd5={flags.m_no_5d_drawdown} entries_allowed={entries_allowed} "
        f"elapsed={elapsed:.2f}s"
    )
    _safe_telegram_send(telegram, Category.STARTUP, body, strategy_label)
    return MorningRoutineResult(
        asof_date=asof_date,
        market_symbol=market_symbol,
        mode=mode,
        flags=flags,
        entries_allowed=entries_allowed,
        daily_rows=len(df),
        elapsed_seconds=elapsed,
    )
