"""LiveTrader: 모든 컴포넌트 통합 운영 클래스.

Iter1~12 의 모든 빌딩 블록(config/cache/fetcher/aggregator/buffer/executor/
tracker/killswitch/notify/monitor/orchestrator)을 단일 entry point 로 연결.
사용자는 LiveTrader 인스턴스를 만든 뒤 매일 run_morning → run_trade 호출하고,
WebSocket 콜백을 on_price/on_fill 로 연결한다.

체결 통보 wiring 의 fill_time HHMMSS→ISO 변환은 KISFillSubscriber 가 담당하며,
LiveTrader 는 이미 변환된 notice 를 받는다고 가정한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as _date

from kis_backtest.live.config.credentials import LiveConfig
from kis_backtest.live.data.bar_aggregator import (
    FiveMinuteBarAggregator,
    RealtimePriceLike,
)
from kis_backtest.live.data.bar_buffer import FiveMinuteBarBuffer
from kis_backtest.live.data.cache import DailyOHLCVCache
from kis_backtest.live.data.fetcher import DailyBarFetcher
from kis_backtest.live.notify.telegram import Category, TelegramClient
from kis_backtest.live.orchestrator.execute_step import (
    LiveOrderExecutor,
    OrderResult,
    execute_step,
)
from kis_backtest.live.orchestrator.fill_handler import FillNoticeLike, handle_fill
from kis_backtest.live.orchestrator.monitors import Api5xxMonitor, WsHealthMonitor
from kis_backtest.live.orchestrator.morning_routine import (
    MorningRoutineResult,
    morning_routine,
)
from kis_backtest.live.orchestrator.trade_step import dry_run_trade_step
from kis_backtest.live.position.tracker import PositionTracker
from kis_backtest.live.risk.killswitch import HaltReason, Killswitch
from kis_backtest.live.signal.engine import SignalEngine

logger = logging.getLogger(__name__)


@dataclass
class LiveTrader:
    config: LiveConfig
    fetcher: DailyBarFetcher
    cache: DailyOHLCVCache
    bar_buffer: FiveMinuteBarBuffer
    aggregator: FiveMinuteBarAggregator
    executor: LiveOrderExecutor
    tracker: PositionTracker
    killswitch: Killswitch
    ws_monitor: WsHealthMonitor
    api_monitor: Api5xxMonitor
    engines: list[SignalEngine]
    telegram: TelegramClient | None = None
    market_symbol: str = "069500"
    strategy_label: str = "composite"
    history_days: int = 120
    max_positions: int = 1

    def run_morning(self, asof_date: _date) -> MorningRoutineResult:
        return morning_routine(
            self.fetcher,
            self.cache,
            self.telegram,
            self.market_symbol,
            asof_date,
            mode=self.config.mode,
            history_days=self.history_days,
            strategy_label=self.strategy_label,
        )

    def run_trade(
        self,
        routine_result: MorningRoutineResult,
        *,
        dry_run: bool = True,
    ) -> list[OrderResult]:
        trade = dry_run_trade_step(
            routine_result,
            self.engines,
            self.telegram,
            strategy_label=self.strategy_label,
        )
        return execute_step(
            trade,
            self.executor,
            self.killswitch,
            self.config.limits.capital_krw,
            self.telegram,
            dry_run=dry_run,
            strategy_label=self.strategy_label,
            max_positions=self.max_positions,
        )

    def on_price(self, symbol: str, price: RealtimePriceLike) -> None:
        self.aggregator.on_price(symbol, price)

    def on_fill(self, notice: FillNoticeLike) -> HaltReason | None:
        return handle_fill(
            notice,
            self.tracker,
            self.killswitch,
            self.telegram,
            ws_disconnect_seconds=self.ws_monitor.disconnect_seconds(),
            api_5xx_count_5min=self.api_monitor.count_5min(),
            strategy_label=self.strategy_label,
        )

    def shutdown(self, asof_date: _date) -> None:
        self.aggregator.flush_all()
        snapshot_path = self.bar_buffer.snapshot(asof_date)
        body = (
            f"shutdown asof={asof_date} "
            f"realized_pnl={int(self.tracker.state.realized_pnl_krw)} "
            f"daily_pnl={int(self.tracker.state.daily_realized_pnl_krw)} "
            f"trades_today={self.tracker.state.trades_today} "
            f"snapshot={snapshot_path}"
        )
        if self.telegram is not None:
            try:
                self.telegram.send(
                    Category.DAILY, body, strategy=self.strategy_label
                )
            except Exception as e:
                logger.error("telegram DAILY send failed: %s", e)


def build_engines(*engines: SignalEngine) -> list[SignalEngine]:
    """편의 빌더 — 신호 엔진을 LiveTrader.engines 에 주입할 list 로 묶기."""
    return list(engines)


__all__ = ["LiveTrader", "build_engines"]
