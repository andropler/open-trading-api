from __future__ import annotations

from typing import Any

import pytest

from kis_backtest.live.config.credentials import TelegramCreds
from kis_backtest.live.notify.telegram import Category, TelegramClient


class FakeTransport:
    def __init__(self, response: dict[str, Any] | None = None, exc: Exception | None = None):
        self.response = response if response is not None else {"ok": True, "result": {}}
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict) -> dict:
        self.calls.append((url, json))
        if self.exc is not None:
            raise self.exc
        return self.response


@pytest.fixture
def creds() -> TelegramCreds:
    return TelegramCreds(bot_token="bot:abc", chat_id="12345")


class TestSend:
    def test_normal_send_succeeds(self, creds):
        t = FakeTransport()
        c = TelegramClient(creds=creds, transport=t)
        c.send(Category.SIGNAL, "entry 005930", now=1000.0)
        assert len(t.calls) == 1
        url, payload = t.calls[0]
        assert "bot:abc/sendMessage" in url
        assert payload["chat_id"] == "12345"
        assert "[SIGNAL][composite]" in payload["text"]
        assert "entry 005930" in payload["text"]

    def test_format_includes_iso_timestamp(self, creds):
        t = FakeTransport()
        c = TelegramClient(creds=creds, transport=t)
        c.send(Category.STARTUP, "boot", now=0.0)
        text = t.calls[0][1]["text"]
        assert "1970-01-01T00:00:00Z" in text


class TestRateLimit:
    def test_within_limit(self, creds):
        t = FakeTransport()
        c = TelegramClient(creds=creds, transport=t, rate_limit_per_minute=3)
        c.send(Category.SIGNAL, "a", now=1000.0)
        c.send(Category.SIGNAL, "b", now=1001.0)
        c.send(Category.SIGNAL, "c", now=1002.0)
        assert len(t.calls) == 3

    def test_exceeds_limit_raises(self, creds):
        t = FakeTransport()
        c = TelegramClient(creds=creds, transport=t, rate_limit_per_minute=2)
        c.send(Category.SIGNAL, "a", now=1000.0)
        c.send(Category.SIGNAL, "b", now=1001.0)
        with pytest.raises(RuntimeError, match="rate limit"):
            c.send(Category.SIGNAL, "c", now=1002.0)

    def test_window_slides(self, creds):
        t = FakeTransport()
        c = TelegramClient(creds=creds, transport=t, rate_limit_per_minute=2)
        c.send(Category.SIGNAL, "a", now=1000.0)
        c.send(Category.SIGNAL, "b", now=1001.0)
        # 60초 후 → 윈도우 비워짐
        c.send(Category.SIGNAL, "c", now=1062.0)
        assert len(t.calls) == 3

    def test_halt_bypasses_rate_limit(self, creds):
        t = FakeTransport()
        c = TelegramClient(creds=creds, transport=t, rate_limit_per_minute=1)
        c.send(Category.SIGNAL, "a", now=1000.0)
        # SIGNAL는 한도 초과지만 HALT는 우회
        c.send(Category.HALT, "killswitch", now=1001.0)
        assert len(t.calls) == 2


class TestErrorHandling:
    def test_transport_exception_raises_runtime(self, creds):
        t = FakeTransport(exc=ConnectionError("net down"))
        c = TelegramClient(creds=creds, transport=t)
        with pytest.raises(RuntimeError, match="telegram send failed"):
            c.send(Category.WARN, "x", now=1000.0)

    def test_api_returns_not_ok(self, creds):
        t = FakeTransport(response={"ok": False, "description": "Bad Request"})
        c = TelegramClient(creds=creds, transport=t)
        with pytest.raises(RuntimeError, match="telegram api"):
            c.send(Category.WARN, "x", now=1000.0)


class TestCategoryValidation:
    def test_invalid_category_type_rejected(self, creds):
        t = FakeTransport()
        c = TelegramClient(creds=creds, transport=t)
        with pytest.raises(ValueError, match="Category"):
            c.send("STARTUP", "boot", now=1000.0)  # type: ignore[arg-type]
