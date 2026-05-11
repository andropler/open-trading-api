from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from kis_backtest.live.data.bar_aggregator import FiveMinuteBarAggregator
from kis_backtest.live.data.bar_buffer import FiveMinuteBarBuffer
from kis_backtest.live.data.price_subscriber import KISPriceSubscriber


@dataclass
class FakePrice:
    symbol: str
    time: str
    price: int
    volume: int


class FakeWsProvider:
    def __init__(self):
        self.callback = None
        self.symbols = None

    def subscribe_price(self, symbols, callback):
        self.symbols = list(symbols)
        self.callback = callback


class TestStart:
    def test_start_passes_symbols_and_aggregator_callback(self):
        buf = FiveMinuteBarBuffer()
        agg = FiveMinuteBarAggregator(buffer=buf, today=date(2026, 5, 7))
        ws = FakeWsProvider()
        sub = KISPriceSubscriber(ws_provider=ws, aggregator=agg)
        sub.start(["005930", "000660"])
        assert ws.symbols == ["005930", "000660"]
        # bound method — '==' 비교 (Python bound method 동등성)
        assert ws.callback == agg.on_price


class TestEndToEnd:
    def test_callback_drives_aggregator(self):
        buf = FiveMinuteBarBuffer()
        agg = FiveMinuteBarAggregator(buffer=buf, today=date(2026, 5, 7))
        ws = FakeWsProvider()
        sub = KISPriceSubscriber(ws_provider=ws, aggregator=agg)
        sub.start(["005930"])
        # WS가 콜백 호출 시뮬
        ws.callback("005930", FakePrice("005930", "093001", 70000, 10))
        ws.callback("005930", FakePrice("005930", "093530", 70500, 5))
        # 09:30 봉이 flush 되어 있어야 함
        assert len(buf.get("005930")) == 1


class TestEmptySymbols:
    def test_no_symbols(self):
        buf = FiveMinuteBarBuffer()
        agg = FiveMinuteBarAggregator(buffer=buf, today=date(2026, 5, 7))
        ws = FakeWsProvider()
        sub = KISPriceSubscriber(ws_provider=ws, aggregator=agg)
        sub.start([])
        assert ws.symbols == []
