"""주문 발행 step: dry_run_trade_step 결과를 받아 KIS submit_order 호출(또는 dry-run).

흐름:
1. trade_result.entries_allowed=False 또는 selected_count=0 → 빈 리스트
2. killswitch HALTed → telegram WARN + 빈 리스트
3. selected[:max_positions] 절단
4. 각 신호에 대해:
   a. 포지션 사이징: slot_capital / entry_price → qty
   b. qty < 1 → OrderResult(reason="insufficient_capital")
   c. dry_run=True → telegram ORDER 'DRY-RUN ...' + OrderResult(reason="dry_run")
   d. dry_run=False → executor.submit_order 호출 → 성공/실패 결과 + telegram ORDER/ERROR

체결 통보(WS) 와 PositionTracker 갱신은 별도 이터레이션.

**Killswitch 계약**:
본 함수는 `killswitch.is_halted()` (HALT.flag 파일 존재) 만 확인한다.
`killswitch.evaluate(metrics, ts)` 의 호출은 외부 운영 루프 책임이며, 보통
체결 통보 처리 직후 PositionTracker 갱신과 함께 호출된다. 운영 루프 구현 시
이 호출을 빠뜨리면 HALT 가 발화되지 않으므로 명시적으로 wire-up 해야 한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Protocol

from kis_backtest.live.notify.telegram import Category, TelegramClient
from kis_backtest.live.orchestrator.trade_step import DryRunTradeStepResult
from kis_backtest.live.risk.killswitch import Killswitch
from kis_backtest.live.signal.models import Signal

logger = logging.getLogger(__name__)


class LiveOrderExecutor(Protocol):
    def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str = "market",
        price: int = 0,
    ) -> str: ...


@dataclass(frozen=True)
class OrderRequest:
    ticker: str
    side: str
    qty: int
    order_type: str
    price: int
    signal_variant: str
    signal_priority: float


@dataclass(frozen=True)
class OrderResult:
    request: OrderRequest
    submitted: bool
    order_id: Optional[str] = None
    error: Optional[str] = None
    reason: Optional[str] = None  # skip/dry_run/insufficient_capital


def _safe_telegram_send(
    telegram: TelegramClient | None, category: Category, body: str, strategy: str
) -> None:
    if telegram is None:
        return
    try:
        telegram.send(category, body, strategy=strategy)
    except Exception as e:
        logger.error("telegram send failed (%s): %s", category.value, e)


def _build_request(sig: Signal, qty: int) -> OrderRequest:
    return OrderRequest(
        ticker=sig.ticker,
        side="buy",
        qty=qty,
        order_type="market",
        price=0,
        signal_variant=sig.variant,
        signal_priority=sig.priority,
    )


def execute_step(
    trade_result: DryRunTradeStepResult,
    executor: LiveOrderExecutor,
    killswitch: Killswitch,
    capital_krw: int,
    telegram: TelegramClient | None = None,
    *,
    dry_run: bool = True,
    strategy_label: str = "composite",
    max_positions: int = 1,
) -> list[OrderResult]:
    if not trade_result.entries_allowed or trade_result.selected_count == 0:
        return []

    if killswitch.is_halted():
        _safe_telegram_send(
            telegram,
            Category.WARN,
            "killswitch HALTed; skipping orders",
            strategy_label,
        )
        return []

    if max_positions <= 0:
        raise ValueError(f"max_positions must be >= 1, got {max_positions}")
    if capital_krw <= 0:
        raise ValueError(f"capital_krw must be positive, got {capital_krw}")

    selected = trade_result.selected[:max_positions]
    slot_capital = capital_krw // max_positions
    results: list[OrderResult] = []

    for sig in selected:
        qty = int(slot_capital / sig.entry_price)
        if qty < 1:
            request = _build_request(sig, qty=0)
            results.append(
                OrderResult(
                    request=request,
                    submitted=False,
                    reason="insufficient_capital",
                )
            )
            continue

        request = _build_request(sig, qty=qty)

        if dry_run:
            _safe_telegram_send(
                telegram,
                Category.ORDER,
                (
                    f"DRY-RUN BUY {sig.ticker} qty={qty} mkt "
                    f"(variant={sig.variant} entry~={int(sig.entry_price)})"
                ),
                strategy_label,
            )
            results.append(
                OrderResult(request=request, submitted=False, reason="dry_run")
            )
            continue

        try:
            order_id = executor.submit_order(
                symbol=sig.ticker,
                side="buy",
                quantity=qty,
                order_type="market",
                price=0,
            )
        except Exception as e:
            _safe_telegram_send(
                telegram,
                Category.ERROR,
                f"order failed {sig.ticker} qty={qty}: {e}",
                strategy_label,
            )
            results.append(
                OrderResult(request=request, submitted=False, error=str(e))
            )
            continue

        _safe_telegram_send(
            telegram,
            Category.ORDER,
            (
                f"BUY {sig.ticker} qty={qty} mkt order_id={order_id} "
                f"(variant={sig.variant})"
            ),
            strategy_label,
        )
        results.append(
            OrderResult(request=request, submitted=True, order_id=order_id)
        )

    return results
