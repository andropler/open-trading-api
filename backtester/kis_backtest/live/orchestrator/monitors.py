"""운영 모니터: WsHealthMonitor + Api5xxMonitor.

handle_fill 의 ws_disconnect_seconds / api_5xx_count_5min 인자에 주입할 값을
실시간으로 집계한다. 시간 인자가 None 이면 time.time() 사용 (테스트는 명시적
주입 권장).
"""

from __future__ import annotations

import time
from collections import deque


class WsHealthMonitor:
    def __init__(self) -> None:
        self._last_disconnect_ts: float | None = None
        self._total_seconds: int = 0

    def on_disconnect(self, ts: float | None = None) -> None:
        if self._last_disconnect_ts is not None:
            return  # 이미 단절 상태 — 중복 호출 무시
        self._last_disconnect_ts = ts if ts is not None else time.time()

    def on_reconnect(self, ts: float | None = None) -> None:
        if self._last_disconnect_ts is None:
            return
        now = ts if ts is not None else time.time()
        self._total_seconds += int(now - self._last_disconnect_ts)
        self._last_disconnect_ts = None

    def disconnect_seconds(self, now: float | None = None) -> int:
        total = self._total_seconds
        if self._last_disconnect_ts is not None:
            t = now if now is not None else time.time()
            total += int(t - self._last_disconnect_ts)
        return total


class Api5xxMonitor:
    def __init__(self, window_seconds: int = 300) -> None:
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be positive, got {window_seconds}")
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()

    def record_5xx(self, ts: float | None = None) -> None:
        t = ts if ts is not None else time.time()
        self._timestamps.append(t)
        self._evict(t)

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def count_5min(self, now: float | None = None) -> int:
        t = now if now is not None else time.time()
        self._evict(t)
        return len(self._timestamps)
