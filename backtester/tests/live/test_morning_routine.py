from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from kis_backtest.live.config.credentials import TelegramCreds
from kis_backtest.live.data.cache import DailyOHLCVCache
from kis_backtest.live.notify.telegram import TelegramClient
from kis_backtest.live.orchestrator import morning_routine


def _trend_bars(start: date, n: int, slope: float, base: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="B")
    closes = [base + i * slope for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1000] * n,
        }
    )


class FakeFetcher:
    def __init__(self, df: pd.DataFrame, raise_exc: Exception | None = None):
        self.df = df
        self.raise_exc = raise_exc

    def fetch_daily(self, symbol, start_date, end_date):
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.df.copy()


class CapturingTransport:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, json: dict) -> dict:
        self.calls.append((url, json))
        if self.fail:
            raise RuntimeError("network down")
        return {"ok": True, "result": {}}


@pytest.fixture
def cache(tmp_path: Path) -> DailyOHLCVCache:
    return DailyOHLCVCache(tmp_path / "daily")


@pytest.fixture
def telegram() -> tuple[TelegramClient, CapturingTransport]:
    transport = CapturingTransport()
    creds = TelegramCreds(bot_token="bot:abc", chat_id="123")
    return TelegramClient(creds=creds, transport=transport), transport


class TestBullScenario:
    def test_uptrend_allows_entries_and_sends_startup(self, cache, telegram):
        client, transport = telegram
        bars = _trend_bars(date(2026, 1, 1), 100, slope=0.5)
        fetcher = FakeFetcher(bars)
        result = morning_routine(fetcher, cache, client, "069500", date(2026, 5, 30))
        assert result.entries_allowed
        assert result.flags.m_bull_20_60
        assert result.daily_rows == 100
        assert result.elapsed_seconds >= 0
        assert len(transport.calls) == 1
        text = transport.calls[0][1]["text"]
        assert "[STARTUP][composite]" in text
        assert "entries_allowed=True" in text
        assert "069500" in text


class TestBearScenario:
    def test_downtrend_blocks_entries(self, cache, telegram):
        client, transport = telegram
        bars = _trend_bars(date(2026, 1, 1), 100, slope=-0.5, base=200.0)
        fetcher = FakeFetcher(bars)
        result = morning_routine(fetcher, cache, client, "069500", date(2026, 5, 30))
        assert not result.entries_allowed
        text = transport.calls[0][1]["text"]
        assert "entries_allowed=False" in text


class TestFetcherFailure:
    def test_runtime_error_re_raised_with_telegram_error_alert(self, cache, telegram):
        client, transport = telegram
        fetcher = FakeFetcher(pd.DataFrame(), raise_exc=RuntimeError("KIS 5xx"))
        with pytest.raises(RuntimeError, match="KIS 5xx"):
            morning_routine(fetcher, cache, client, "069500", date(2026, 5, 30))
        assert len(transport.calls) == 1
        sent_text = transport.calls[0][1]["text"]
        assert "[ERROR][composite]" in sent_text
        assert "refresh_market_index failed" in sent_text


class TestTelegramFailure:
    def test_telegram_failure_does_not_break_routine(self, cache):
        bars = _trend_bars(date(2026, 1, 1), 100, slope=0.5)
        fetcher = FakeFetcher(bars)
        transport = CapturingTransport(fail=True)
        creds = TelegramCreds(bot_token="bot:abc", chat_id="123")
        client = TelegramClient(creds=creds, transport=transport)
        # 텔레그램이 실패해도 결과는 정상 반환
        result = morning_routine(fetcher, cache, client, "069500", date(2026, 5, 30))
        assert result.entries_allowed


class TestNoTelegram:
    def test_optional_telegram_none(self, cache):
        bars = _trend_bars(date(2026, 1, 1), 100, slope=0.5)
        fetcher = FakeFetcher(bars)
        # telegram=None 도 허용
        result = morning_routine(fetcher, cache, None, "069500", date(2026, 5, 30))
        assert result.entries_allowed


class TestCacheReuse:
    def test_idempotent_same_inputs(self, cache, telegram):
        client, transport = telegram
        bars = _trend_bars(date(2026, 1, 1), 100, slope=0.5)
        fetcher = FakeFetcher(bars)
        r1 = morning_routine(fetcher, cache, client, "069500", date(2026, 5, 30))
        r2 = morning_routine(fetcher, cache, client, "069500", date(2026, 5, 30))
        assert r1.flags == r2.flags
        assert r1.daily_rows == r2.daily_rows


class TestStrategyLabel:
    def test_label_propagates_to_telegram(self, cache, telegram):
        client, transport = telegram
        bars = _trend_bars(date(2026, 1, 1), 100, slope=0.5)
        fetcher = FakeFetcher(bars)
        morning_routine(
            fetcher, cache, client, "069500", date(2026, 5, 30), strategy_label="custom_label"
        )
        text = transport.calls[0][1]["text"]
        assert "[STARTUP][custom_label]" in text


class TestMode:
    def test_mode_in_body_and_result(self, cache, telegram):
        client, transport = telegram
        bars = _trend_bars(date(2026, 1, 1), 100, slope=0.5)
        fetcher = FakeFetcher(bars)
        result = morning_routine(
            fetcher, cache, client, "069500", date(2026, 5, 30), mode="vps"
        )
        assert result.mode == "vps"
        text = transport.calls[0][1]["text"]
        assert "mode=vps" in text

    def test_mode_in_error_alert(self, cache, telegram):
        client, transport = telegram
        fetcher = FakeFetcher(pd.DataFrame(), raise_exc=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            morning_routine(
                fetcher, cache, client, "069500", date(2026, 5, 30), mode="prod"
            )
        text = transport.calls[0][1]["text"]
        assert "mode=prod" in text
