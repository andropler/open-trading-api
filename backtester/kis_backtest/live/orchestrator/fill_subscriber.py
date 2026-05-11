"""KIS WebSocket subscribe_fills → handle_fill 콜백 wiring + HHMMSS→ISO 변환.

KIS websocket 의 FillNotice.fill_time 은 STCK_CNTG_HOUR (HHMMSS 6자리). 본
어댑터가 today 날짜와 결합해 ISO 8601 로 변환한 뒤 handle_fill 에 전달한다.
WsHealthMonitor, Api5xxMonitor 에서 매 fill 시 metrics 를 주입한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date as _date
from typing import Callable, Protocol

from kis_backtest.live.notify.telegram import TelegramClient
from kis_backtest.live.orchestrator.fill_handler import (
    FillNoticeLike,
    handle_fill,
)
from kis_backtest.live.orchestrator.monitors import Api5xxMonitor, WsHealthMonitor
from kis_backtest.live.position.tracker import PositionTracker
from kis_backtest.live.risk.killswitch import Killswitch

logger = logging.getLogger(__name__)


class _WsFillSubscriber(Protocol):
    """KIS websocket.subscribe_fills 호환 Protocol (테스트 격리용)."""

    def subscribe_fills(
        self, callback: Callable[[FillNoticeLike], None]
    ) -> None: ...


def hhmmss_to_iso(today: _date, fill_time: str) -> str:
    """HHMMSS 6자리 → today + ISO 8601 변환. 6자리 아니면 원본 반환 (ISO 가정).

    예: today=2026-05-06, fill_time='093500' → '2026-05-06T09:35:00'
    """
    if len(fill_time) == 6 and fill_time.isdigit():
        return f"{today.isoformat()}T{fill_time[:2]}:{fill_time[2:4]}:{fill_time[4:6]}"
    return fill_time


@dataclass
class KISFillSubscriber:
    ws_provider: _WsFillSubscriber
    tracker: PositionTracker
    killswitch: Killswitch
    ws_monitor: WsHealthMonitor
    api_monitor: Api5xxMonitor
    telegram: TelegramClient | None = None
    today: _date | None = None
    strategy_label: str = "composite"

    def start(self) -> None:
        self.ws_provider.subscribe_fills(self._on_notice)

    def set_today(self, today: _date) -> None:
        """장 시작 또는 자정 롤오버 시 호출해 fill_time 변환의 날짜 기준 갱신."""
        self.today = today

    def _on_notice(self, notice: FillNoticeLike) -> None:
        if self.today is None:
            logger.error(
                "KISFillSubscriber.today is None; fill_time=%s left as-is",
                notice.fill_time,
            )
            adjusted = notice
        else:
            new_time = hhmmss_to_iso(self.today, notice.fill_time)
            if new_time != notice.fill_time:
                adjusted = replace(notice, fill_time=new_time) if hasattr(
                    notice, "__dataclass_fields__"
                ) else _OverrideTime(notice, new_time)
            else:
                adjusted = notice
        handle_fill(
            adjusted,
            self.tracker,
            self.killswitch,
            self.telegram,
            ws_disconnect_seconds=self.ws_monitor.disconnect_seconds(),
            api_5xx_count_5min=self.api_monitor.count_5min(),
            strategy_label=self.strategy_label,
        )


class _OverrideTime:
    """non-dataclass FillNotice 호환 — fill_time 만 덮어쓰는 lazy proxy."""

    def __init__(self, base: FillNoticeLike, new_fill_time: str):
        self._base = base
        self.fill_time = new_fill_time

    def __getattr__(self, name: str):
        return getattr(self._base, name)
