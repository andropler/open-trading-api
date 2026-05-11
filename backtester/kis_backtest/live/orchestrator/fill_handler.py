"""체결 통보 처리: FillNotice → PositionTracker 갱신 → killswitch.evaluate 트리거.

execute_step 이 주문을 발행한 뒤, KIS WebSocket H0STCNI0/H0STCNI9 콜백이
FillNotice 를 전달한다. 이 함수가 tracker 를 갱신하고 누적 손익으로 killswitch
를 평가한다. WebSocket 구독 wiring 은 다음 이터레이션 책임이며, 본 함수는
순수 비즈니스 로직만 담당해 단위 테스트가 KIS 의존성 0 으로 가능.

KIS side 코드: '01'=매도, '02'=매수.
"""

from __future__ import annotations

import logging
from typing import Protocol

from kis_backtest.live.notify.telegram import Category, TelegramClient
from kis_backtest.live.position.tracker import PositionTracker
from kis_backtest.live.risk.killswitch import HaltReason, Killswitch, TradingMetrics

logger = logging.getLogger(__name__)


class FillNoticeLike(Protocol):
    """KIS providers/kis/websocket FillNotice 와 동일 시그니처 (테스트 격리용).

    **fill_time 형식 계약**: ISO 8601 (예: "2026-05-06T09:35:00") 권장.
    KIS 원본 FillNotice.fill_time 은 HHMMSS 6자리 문자열(STCK_CNTG_HOUR) 이므로
    WebSocket wiring 이터레이션에서 datetime.now() 의 날짜와 결합해 ISO 로
    변환한 뒤 본 함수에 전달해야 한다. 미변환 HHMMSS 그대로 넘기면 killswitch
    에 기록되는 ts 가 날짜 정보 없이 오염된다 (런타임 오류는 아니지만 의미론적
    오류).
    """

    customer_id: str
    account_no: str
    order_no: str
    order_qty: int
    side: str  # '01'=매도, '02'=매수
    symbol: str
    fill_qty: int
    fill_price: int
    fill_time: str
    is_fill: bool
    is_rejected: bool


def _safe_telegram_send(
    telegram: TelegramClient | None, category: Category, body: str, strategy: str
) -> None:
    if telegram is None:
        return
    try:
        telegram.send(category, body, strategy=strategy)
    except Exception as e:
        logger.error("telegram send failed (%s): %s", category.value, e)


def _build_metrics(
    tracker: PositionTracker,
    ws_disconnect_seconds: int,
    api_5xx_count_5min: int,
) -> TradingMetrics:
    return TradingMetrics(
        daily_realized_pnl_krw=tracker.state.daily_realized_pnl_krw,
        cumulative_realized_pnl_krw=tracker.state.realized_pnl_krw,
        consecutive_losses=tracker.state.consecutive_losses,
        ws_disconnect_seconds=ws_disconnect_seconds,
        api_5xx_count_5min=api_5xx_count_5min,
    )


def handle_fill(
    notice: FillNoticeLike,
    tracker: PositionTracker,
    killswitch: Killswitch,
    telegram: TelegramClient | None = None,
    *,
    ws_disconnect_seconds: int = 0,
    api_5xx_count_5min: int = 0,
    strategy_label: str = "composite",
) -> HaltReason | None:
    """체결 통보 1건 처리. killswitch 가 발화하면 HaltReason 반환, 아니면 None.

    **호출자 책임 (WebSocket wiring)**:
    - ws_disconnect_seconds: WebSocket 단절 누적 초. 호출자(WS 루프)가 별도
      모니터로 집계해 매 체결마다 주입. 0(기본값)으로 두면 ws_disconnect
      killswitch 조건이 영구 비활성화되므로 wiring 시 반드시 wire-up.
    - api_5xx_count_5min: 5분 윈도우 5xx 카운트. KIS API 호출자 측 모니터에서
      집계 후 주입. 동일하게 0 디폴트는 해당 조건 비활성화.
    """
    if notice.is_rejected:
        _safe_telegram_send(
            telegram,
            Category.ERROR,
            (
                f"order rejected order_no={notice.order_no} "
                f"symbol={notice.symbol} qty={notice.order_qty}"
            ),
            strategy_label,
        )
        return None

    if not notice.is_fill:
        _safe_telegram_send(
            telegram,
            Category.ORDER,
            (
                f"ACK order_no={notice.order_no} symbol={notice.symbol} "
                f"qty={notice.order_qty}"
            ),
            strategy_label,
        )
        return None

    if notice.side not in ("01", "02"):
        raise ValueError(
            f"side must be '01'(sell) or '02'(buy), got {notice.side!r}"
        )

    if notice.side == "02":  # 매수 체결
        tracker.open_position(
            symbol=notice.symbol,
            qty=notice.fill_qty,
            price=float(notice.fill_price),
            ts=notice.fill_time,
        )
        _safe_telegram_send(
            telegram,
            Category.ORDER,
            (
                f"FILL BUY {notice.symbol} qty={notice.fill_qty} "
                f"price={notice.fill_price} order_no={notice.order_no}"
            ),
            strategy_label,
        )
    else:  # '01' 매도 체결
        net = tracker.close_position(
            symbol=notice.symbol,
            qty=notice.fill_qty,
            price=float(notice.fill_price),
            ts=notice.fill_time,
        )
        gross = float(notice.fill_price) * notice.fill_qty
        pnl_pct = (net / gross) * 100.0 if gross > 0 else 0.0
        _safe_telegram_send(
            telegram,
            Category.EXIT,
            (
                f"FILL SELL {notice.symbol} qty={notice.fill_qty} "
                f"price={notice.fill_price} net={int(net)} ({pnl_pct:+.2f}%) "
                f"order_no={notice.order_no}"
            ),
            strategy_label,
        )

    metrics = _build_metrics(tracker, ws_disconnect_seconds, api_5xx_count_5min)
    halt = killswitch.evaluate(metrics, notice.fill_time)
    if halt is not None:
        _safe_telegram_send(
            telegram,
            Category.HALT,
            (
                f"HALT triggered condition={halt.condition_id} "
                f"value={halt.value:.2f} threshold={halt.threshold:.2f}"
            ),
            strategy_label,
        )
    return halt
