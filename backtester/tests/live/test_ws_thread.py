from __future__ import annotations

import threading
import time

import pytest

from kis_backtest.live.orchestrator.ws_thread import WsThreadLauncher


class FakeWebSocket:
    def __init__(self, raise_on_start: Exception | None = None):
        self.subscribed_prices: list[tuple[list[str], object]] = []
        self.subscribed_fills: list[object] = []
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.raise_on_start = raise_on_start

    def subscribe_price(self, symbols, callback):
        self.subscribed_prices.append((list(symbols), callback))

    def subscribe_fills(self, callback):
        self.subscribed_fills.append(callback)

    def start(self, timeout=None):
        self.started.set()
        if self.raise_on_start is not None:
            raise self.raise_on_start
        # blocking until stop
        self.stopped.wait(timeout=2.0)

    def stop(self):
        self.stopped.set()


class TestSubscribe:
    def test_subscribe_price_passes_through(self):
        ws = FakeWebSocket()
        launcher = WsThreadLauncher(ws=ws)
        cb = lambda sym, price: None  # noqa: E731
        launcher.subscribe_price(["005930"], cb)
        assert ws.subscribed_prices == [(["005930"], cb)]

    def test_subscribe_fills_passes_through(self):
        ws = FakeWebSocket()
        launcher = WsThreadLauncher(ws=ws)
        cb = lambda notice: None  # noqa: E731
        launcher.subscribe_fills(cb)
        assert ws.subscribed_fills == [cb]


class TestThreadLifecycle:
    def test_start_runs_in_separate_thread(self):
        ws = FakeWebSocket()
        launcher = WsThreadLauncher(ws=ws)
        launcher.start()
        # WS.start() 가 별도 thread 에서 호출됐는지
        assert ws.started.wait(timeout=1.0)
        assert launcher.alive
        launcher.stop()
        assert not launcher.alive

    def test_double_start_raises(self):
        ws = FakeWebSocket()
        launcher = WsThreadLauncher(ws=ws)
        launcher.start()
        with pytest.raises(RuntimeError, match="already started"):
            launcher.start()
        launcher.stop()

    def test_stop_before_start_noop(self):
        ws = FakeWebSocket()
        launcher = WsThreadLauncher(ws=ws)
        launcher.stop()  # 예외 없이 통과
        assert not launcher.alive

    def test_ws_crash_does_not_propagate(self):
        # WS thread 에서 raise 해도 main thread 영향 X
        ws = FakeWebSocket(raise_on_start=RuntimeError("boom"))
        launcher = WsThreadLauncher(ws=ws)
        launcher.start()
        # thread 가 정상 종료 (예외 잡힘)
        time.sleep(0.2)
        launcher.stop()
        assert not launcher.alive
