"""dry-run 트레이더 step: morning_routine 결과를 받아 신호 엔진 호출 + 텔레그램 SIGNAL.

morning_routine 의 짝. entries_allowed=True 인 날만 엔진을 호출해 신호를 모은다.
실 주문 발행은 다음 이터레이션이며, 본 함수는 알림과 결과 dataclass 만 반환한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Iterable

import pandas as pd

from kis_backtest.live.notify.telegram import Category, TelegramClient
from kis_backtest.live.orchestrator.morning_routine import MorningRoutineResult
from kis_backtest.live.signal.engine import SignalEngine, compose_signals
from kis_backtest.live.signal.models import BASE_CONFIG, CompositeConfig, Signal

logger = logging.getLogger(__name__)


_TELEGRAM_MAX_LEN = 4000  # Telegram Bot API 한도 4096 - 안전 여유


@dataclass(frozen=True)
class DryRunTradeStepResult:
    asof_date: _date
    entries_allowed: bool
    # candidates_count: 모든 엔진의 raw 후보 합산 (variant/hhmm 필터 적용 전)
    candidates_count: int
    # selected_count: BASE_CONFIG 필터 통과 후 신호 수
    # 주의: max_positions 절단은 본 step 에서 수행하지 않음. 주문 발행 단계 책임.
    selected_count: int
    selected: tuple[Signal, ...] = field(default_factory=tuple)


def _safe_telegram_send(
    telegram: TelegramClient | None, category: Category, body: str, strategy: str
) -> None:
    if telegram is None:
        return
    try:
        telegram.send(category, body, strategy=strategy)
    except Exception as e:
        logger.error("telegram send failed (%s): %s", category.value, e)


def _format_signal_body(selected: list[Signal]) -> str:
    lines = [f"{len(selected)} candidates:"]
    for i, sig in enumerate(selected, 1):
        tp = (
            f"tp={sig.profile.take_profit_pct}%"
            if sig.profile.take_profit_pct is not None
            else "tp=trail"
        )
        lines.append(
            f"{i}.{sig.ticker} {sig.variant} hhmm={sig.entry_hhmm} "
            f"entry={int(sig.entry_price)} stop={int(sig.stop_price)} "
            f"sl={sig.profile.stop_loss_pct}% {tp} prio={sig.priority}"
        )
    body = " | ".join(lines)
    if len(body) > _TELEGRAM_MAX_LEN:
        body = body[: _TELEGRAM_MAX_LEN - 16] + "... (truncated)"
    return body


def dry_run_trade_step(
    routine_result: MorningRoutineResult,
    engines: Iterable[SignalEngine],
    telegram: TelegramClient | None = None,
    *,
    config: CompositeConfig = BASE_CONFIG,
    strategy_label: str = "composite",
) -> DryRunTradeStepResult:
    asof = routine_result.asof_date

    if not routine_result.entries_allowed:
        return DryRunTradeStepResult(
            asof_date=asof,
            entries_allowed=False,
            candidates_count=0,
            selected_count=0,
            selected=(),
        )

    engines_list = list(engines)
    if not engines_list:
        return DryRunTradeStepResult(
            asof_date=asof,
            entries_allowed=True,
            candidates_count=0,
            selected_count=0,
            selected=(),
        )

    asof_ts = pd.Timestamp(asof)
    raw_total = sum(len(e.candidate_signals(asof_ts)) for e in engines_list)
    selected = compose_signals(engines_list, asof_ts, config)

    if selected:
        _safe_telegram_send(
            telegram,
            Category.SIGNAL,
            _format_signal_body(selected),
            strategy_label,
        )

    return DryRunTradeStepResult(
        asof_date=asof,
        entries_allowed=True,
        candidates_count=raw_total,
        selected_count=len(selected),
        selected=tuple(selected),
    )
