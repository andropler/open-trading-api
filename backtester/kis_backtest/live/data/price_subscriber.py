"""KIS subscribe_price → FiveMinuteBarAggregator.on_price wiring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from kis_backtest.live.data.bar_aggregator import (
    FiveMinuteBarAggregator,
    RealtimePriceLike,
)


class _PriceWsProvider(Protocol):
    def subscribe_price(
        self,
        symbols: list[str],
        callback: Callable[[str, RealtimePriceLike], None],
    ) -> None: ...


@dataclass
class KISPriceSubscriber:
    ws_provider: _PriceWsProvider
    aggregator: FiveMinuteBarAggregator

    def start(self, symbols: list[str]) -> None:
        self.ws_provider.subscribe_price(symbols, self.aggregator.on_price)
