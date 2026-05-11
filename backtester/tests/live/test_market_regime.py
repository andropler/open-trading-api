from __future__ import annotations

import pandas as pd
import pytest

from kis_backtest.live.regime.market_regime import compute_flags


def _make_daily(closes: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=len(closes), freq="B")
    return pd.DataFrame({"date": dates, "close": closes})


class TestStrongBull:
    def test_clear_uptrend_passes(self):
        # 100일 우상향 + 마지막 5일도 상승
        closes = [100.0 + i * 0.5 for i in range(100)]
        daily = _make_daily(closes)
        asof = daily["date"].iloc[-1] + pd.Timedelta(days=1)
        flags = compute_flags(daily, asof)
        assert flags.m_bull_20_60
        assert flags.m_no_1d_shock
        assert flags.m_no_5d_drawdown
        assert flags.passes_base_gate()


class TestBearMarket:
    def test_downtrend_fails_bull(self):
        # 100일 우하향
        closes = [200.0 - i * 0.5 for i in range(100)]
        daily = _make_daily(closes)
        asof = daily["date"].iloc[-1] + pd.Timedelta(days=1)
        flags = compute_flags(daily, asof)
        assert not flags.m_bull_20_60

    def test_recent_pullback_fails_bull(self):
        # 80일 상승 후 마지막 20일 하락 → sma20 > sma60 깨짐
        up = [100.0 + i * 0.5 for i in range(80)]
        down = [up[-1] - i * 0.8 for i in range(1, 21)]
        daily = _make_daily(up + down)
        asof = daily["date"].iloc[-1] + pd.Timedelta(days=1)
        flags = compute_flags(daily, asof)
        assert not flags.m_bull_20_60


class TestInsufficientHistory:
    def test_fewer_than_60_returns_all_false(self):
        closes = [100.0 + i for i in range(30)]
        daily = _make_daily(closes)
        asof = daily["date"].iloc[-1] + pd.Timedelta(days=1)
        flags = compute_flags(daily, asof)
        assert not flags.m_bull_20_60
        assert not flags.m_no_1d_shock
        assert not flags.m_no_5d_drawdown


class TestShock:
    def test_one_day_minus_2pct_fails(self):
        # 안정 우상향 후 마지막 일 -2.5%
        closes = [100.0 + i * 0.3 for i in range(99)]
        closes.append(closes[-1] * 0.975)  # -2.5%
        daily = _make_daily(closes)
        asof = daily["date"].iloc[-1] + pd.Timedelta(days=1)
        flags = compute_flags(daily, asof)
        assert not flags.m_no_1d_shock


class TestDrawdown:
    def test_5day_drawdown_minus_4pct_fails(self):
        # 우상향 후 마지막 5일에 4% 하락
        closes = [100.0 + i * 0.3 for i in range(95)]
        peak = closes[-1]
        for pct in [0.0, -0.01, -0.02, -0.03, -0.04]:
            closes.append(peak * (1 + pct))
        daily = _make_daily(closes)
        asof = daily["date"].iloc[-1] + pd.Timedelta(days=1)
        flags = compute_flags(daily, asof)
        assert not flags.m_no_5d_drawdown


class TestLookbackShift:
    def test_asof_date_row_excluded(self):
        # 99일 우상향 + asof 당일에만 -90% 폭락. asof 데이터가 제외되므로
        # 99일 우상향 데이터로 평가 → m_bull_20_60 True 가 정답.
        closes = [100.0 + i * 0.5 for i in range(99)]
        daily = _make_daily(closes + [10.0])
        asof_date = daily["date"].iloc[-1]
        flags = compute_flags(daily, asof_date)
        assert flags.m_bull_20_60, "asof 당일 폭락이 잘못 포함되어 bull 플래그가 꺼짐"

    def test_asof_date_row_included_when_bug(self):
        # 검증의 대조군: 동일 데이터에서 asof+1로 평가하면 폭락일이 포함되어 bull False
        closes = [100.0 + i * 0.5 for i in range(99)]
        daily = _make_daily(closes + [10.0])
        asof_date = daily["date"].iloc[-1] + pd.Timedelta(days=1)
        flags = compute_flags(daily, asof_date)
        assert not flags.m_bull_20_60


class TestColumnValidation:
    def test_missing_close_raises(self):
        daily = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=70), "open": range(70)})
        with pytest.raises(ValueError, match="close"):
            compute_flags(daily, "2026-04-01")

    def test_missing_date_raises(self):
        daily = pd.DataFrame({"close": [100.0] * 70})
        with pytest.raises(ValueError, match="date"):
            compute_flags(daily, "2026-04-01")
