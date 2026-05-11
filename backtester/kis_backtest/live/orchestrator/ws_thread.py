"""KIS WebSocket thread launcher.

KIS WS 는 asyncio.run() 기반 blocking. 메인 루프(run_trading_day)와 병렬로
실행하려면 별도 daemon thread 에서 start() 호출. 동일 KISWebSocket 인스턴스에
price + fill 콜백을 모두 등록한 뒤 thread 1개에서 처리.

본 모듈은 KIS provider 의존성을 직접 import (실제 운영 모듈). 단위 테스트는
mock KISWebSocket-like 객체를 주입해 검증.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)


class _WebSocketLike(Protocol):
    """KISWebSocket 호환 Protocol — 테스트 격리용."""

    def subscribe_price(
        self, symbols: list[str], callback: Callable[[str, object], None]
    ) -> None: ...

    def subscribe_fills(self, callback: Callable[[object], None]) -> None: ...

    def start(self, timeout: Optional[float] = None) -> None: ...

    def stop(self) -> None: ...


@dataclass
class WsThreadLauncher:
    """WebSocket 을 별도 thread 로 띄워 main loop 와 병렬 동작.

    사용 예:
        launcher = WsThreadLauncher(ws=KISWebSocket.from_auth(auth, hts_id))
        launcher.subscribe_price(universe, trader.on_price)
        launcher.subscribe_fills(fill_subscriber._on_notice)
        launcher.start()
        try:
            run_trading_day(trader, ...)
        finally:
            launcher.stop()
    """

    ws: _WebSocketLike
    thread_name: str = "kis-ws"
    _thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    def subscribe_price(
        self, symbols: list[str], callback: Callable[[str, object], None]
    ) -> None:
        self.ws.subscribe_price(symbols, callback)

    def subscribe_fills(self, callback: Callable[[object], None]) -> None:
        self.ws.subscribe_fills(callback)

    def start(self, timeout: Optional[float] = None) -> None:
        if self._started:
            raise RuntimeError("WsThreadLauncher already started")

        def _run() -> None:
            try:
                self.ws.start(timeout=timeout)
            except Exception as e:
                logger.error("WS thread crashed: %s", e)

        self._thread = threading.Thread(target=_run, daemon=True, name=self.thread_name)
        self._thread.start()
        self._started = True

    def stop(self, join_timeout: float = 5.0) -> None:
        if not self._started:
            return
        try:
            self.ws.stop()
        except Exception as e:
            logger.error("WS stop failed: %s", e)
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None
        self._started = False

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


__all__ = ["WsThreadLauncher"]
