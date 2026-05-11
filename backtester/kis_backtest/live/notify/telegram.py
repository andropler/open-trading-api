"""텔레그램 봇을 통한 운영 알림.

8종 카테고리 (STARTUP / SIGNAL / ORDER / EXIT / WARN / ERROR / HALT / DAILY).
HALT 만 분당 한도를 우회한다. 송신 실패 시 RuntimeError 발생, 재시도/큐 없음.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

import httpx

from kis_backtest.live.config.credentials import TelegramCreds


class Category(str, Enum):
    STARTUP = "STARTUP"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    EXIT = "EXIT"
    WARN = "WARN"
    ERROR = "ERROR"
    HALT = "HALT"
    DAILY = "DAILY"


_CRITICAL: set[Category] = {Category.HALT}


class TelegramTransport(Protocol):
    def post(self, url: str, json: dict) -> dict: ...


@dataclass
class HttpxTransport:
    timeout: float = 10.0

    def post(self, url: str, json: dict) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(url, json=json)
            r.raise_for_status()
            return r.json()


@dataclass
class TelegramClient:
    creds: TelegramCreds
    transport: TelegramTransport
    rate_limit_per_minute: int = 20
    _sent_ts: deque = field(default_factory=deque, init=False, repr=False)

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.creds.bot_token}/sendMessage"

    def send(
        self,
        category: Category,
        body: str,
        *,
        strategy: str = "composite",
        now: float | None = None,
    ) -> None:
        if not isinstance(category, Category):
            raise ValueError(f"category must be Category enum, got {type(category).__name__}")
        ts = now if now is not None else time.time()
        if category not in _CRITICAL:
            self._enforce_rate_limit(ts)
        msg = self._format(category, body, strategy, ts)
        try:
            response = self.transport.post(
                self.base_url,
                {"chat_id": self.creds.chat_id, "text": msg},
            )
        except Exception as e:
            # str(e) 가 httpx 예외인 경우 URL+토큰을 포함할 수 있어 type 만 노출.
            raise RuntimeError(f"telegram send failed ({type(e).__name__})") from e
        if not response.get("ok"):
            err_code = response.get("error_code")
            err_desc = response.get("description", "unknown")
            raise RuntimeError(f"telegram api error code={err_code} desc={err_desc}")
        self._sent_ts.append(ts)

    def _enforce_rate_limit(self, ts: float) -> None:
        cutoff = ts - 60.0
        while self._sent_ts and self._sent_ts[0] < cutoff:
            self._sent_ts.popleft()
        if len(self._sent_ts) >= self.rate_limit_per_minute:
            raise RuntimeError(
                f"telegram rate limit hit ({self.rate_limit_per_minute}/min). "
                "Drop or downgrade the message; do not retry."
            )

    @staticmethod
    def _format(category: Category, body: str, strategy: str, ts: float) -> str:
        ts_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"[{category.value}][{strategy}][{ts_iso}] {body}"
