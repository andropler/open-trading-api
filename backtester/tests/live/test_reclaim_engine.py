from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from kis_backtest.live.signal.reclaim_engine import (
    IntradayReclaimParams,
    LiveReclaimEngine,
)


def _daily_with_event(event_date: date, n_days: int = 25) -> pd.DataFrame:
    """event_date 에서 +0.20 갭 + 거래대금 충분 + 최근 +0.30 수익률.

    25일 안정 후 마지막 일에 급등. recent_return_window=3 이라 event_date-3 ~ event_date 가 +30% 이상이어야 함.
    """
    rows = []
    base = 6_000.0
    # 22일: 천천히 상승 (recent_return_min=0.25 만족하도록)
    for i in range(n_days - 3):
        rows.append(
            {
                "timestamp": pd.Timestamp(event_date) - timedelta(days=n_days - i),
                "open": base,
                "high": base * 1.01,
                "low": base * 0.99,
                "close": base,
                "volume": 1_500_000,  # 평균 거래량
            }
        )
    # event_date-2 ~ event_date-1: 천천히 상승하다가
    rows.append(
        {
            "timestamp": pd.Timestamp(event_date) - timedelta(days=3),
            "open": base * 1.00,
            "high": base * 1.01,
            "low": base * 0.99,
            "close": base * 1.00,
            "volume": 1_500_000,
        }
    )
    rows.append(
        {
            "timestamp": pd.Timestamp(event_date) - timedelta(days=2),
            "open": base * 1.01,
            "high": base * 1.02,
            "low": base * 1.00,
            "close": base * 1.01,
            "volume": 1_500_000,
        }
    )
    # event_date-1: 급등 — event_return ≥ 0.15, recent_return(3일 누적) ≥ 0.30
    event_close = base * 1.31  # 3일 누적 +31%
    event_row = {
        "timestamp": pd.Timestamp(event_date) - timedelta(days=1),
        "open": base * 1.05,
        "high": event_close * 1.02,
        "low": base * 1.04,
        "close": event_close,
        "volume": 6_000_000,  # 평균 대비 4x
    }
    rows.append(event_row)
    # asof 당일 행 (장중 진행 시점이라도 KIS 일봉 API가 당일 행을 반환)
    rows.append(
        {
            "timestamp": pd.Timestamp(event_date),
            "open": event_close * 1.05,  # 갭 +5%
            "high": event_close * 1.06,
            "low": event_close * 0.99,
            "close": event_close * 1.05,
            "volume": 1_500_000,
        }
    )
    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return df


def _intraday_reclaim_pattern(asof: date, base_price: float) -> pd.DataFrame:
    """asof 일자 5m 봉 — 09:00~10:30, 풀백(VWAP 아래) → reclaim(VWAP 위 + 거래량 ≥ 2x).

    base_price: 시초가 (전일 종가에 +5% 적용된 값을 가정)
    """
    bars = []
    # 09:00~09:25 (6 봉): 시작 후 풀백 — close 가 VWAP 아래로
    open_prices = [
        base_price * (1 - 0.005 * i) for i in range(6)
    ]  # 점진적 하락
    for i in range(6):
        t = datetime.combine(asof, datetime.min.time()) + timedelta(
            hours=9, minutes=i * 5
        )
        op = open_prices[i]
        bars.append(
            {
                "timestamp": pd.Timestamp(t),
                "open": op,
                "high": op * 1.002,
                "low": op * 0.997,  # 풀백 — vwap 아래 close
                "close": op * 0.998,
                "volume": 5_000,  # 평균 거래량
            }
        )
    # 09:30 (인덱스 6): reclaim bar — close > vwap, bullish, volume ≥ 2x
    reclaim_t = datetime.combine(asof, datetime.min.time()) + timedelta(
        hours=9, minutes=30
    )
    reclaim_open = base_price * 0.992
    reclaim_close = base_price * 1.008  # vwap 위로 반등
    bars.append(
        {
            "timestamp": pd.Timestamp(reclaim_t),
            "open": reclaim_open,
            "high": reclaim_close * 1.002,
            "low": reclaim_open * 0.998,
            "close": reclaim_close,
            "volume": 15_000,  # 평균(5_000)의 3배
        }
    )
    # 09:35: 진입 bar (signal next_idx) — bullish 추가 1개
    entry_t = datetime.combine(asof, datetime.min.time()) + timedelta(
        hours=9, minutes=35
    )
    bars.append(
        {
            "timestamp": pd.Timestamp(entry_t),
            "open": reclaim_close,
            "high": reclaim_close * 1.005,
            "low": reclaim_close * 0.998,
            "close": reclaim_close * 1.003,
            "volume": 8_000,
        }
    )
    # 추가: vol_avg_window=80 만족 위해 더 많은 봉 누적 필요 → 과거일자 5m 봉
    extra = []
    for past in range(1, 6):  # 5일 전부터
        past_date = asof - timedelta(days=past)
        for h in range(9, 15):
            for m in range(0, 60, 5):
                t = datetime.combine(past_date, datetime.min.time()) + timedelta(
                    hours=h, minutes=m
                )
                extra.append(
                    {
                        "timestamp": pd.Timestamp(t),
                        "open": base_price * 0.95,
                        "high": base_price * 0.96,
                        "low": base_price * 0.94,
                        "close": base_price * 0.95,
                        "volume": 5_000,
                    }
                )
    df = pd.DataFrame(extra + bars).sort_values("timestamp").reset_index(drop=True)
    return df


class TestEmptyData:
    def test_no_data_returns_empty(self):
        engine = LiveReclaimEngine()
        assert engine.candidate_signals(pd.Timestamp("2026-05-11")) == []

    def test_no_intraday_returns_empty(self):
        engine = LiveReclaimEngine()
        engine.set_data(daily_data={"005930": pd.DataFrame()}, intraday_data={})
        assert engine.candidate_signals(date(2026, 5, 11)) == []


class TestReclaimPattern:
    def test_pattern_produces_signal(self):
        asof = date(2026, 5, 11)
        daily = _daily_with_event(asof, n_days=30)
        # 다음날 시초가 = 전일 종가 * 1.05 (gap_max=0.08 만족)
        # iloc[-1] = asof 당일 (mock), iloc[-2] = event_date-1 (급등일)
        prev_close = float(daily.iloc[-2]["close"])
        base = prev_close * 1.05
        intraday = _intraday_reclaim_pattern(asof, base)
        engine = LiveReclaimEngine()
        engine.set_data(
            daily_data={"005930": daily}, intraday_data={"005930": intraday}
        )
        signals = engine.candidate_signals(asof)
        assert len(signals) >= 1
        sig = signals[0]
        assert sig.ticker == "005930"
        assert sig.variant == "reclaim_strict"
        assert sig.source == "reclaim"
        assert sig.stop_price < sig.entry_price
        assert sig.profile.stop_loss_pct == 3.0
        assert sig.profile.take_profit_pct == 10.0
        # ExitProfile 의 trail_activation, trail_pct, max_hold_days 모두 params 와 동일
        assert sig.profile.trail_activation_pct == 5.0
        assert sig.profile.trail_pct == 4.0
        assert sig.profile.max_hold_days == 1


class TestParams:
    def test_default_params_match_reclaim_strict(self):
        p = IntradayReclaimParams()
        # alpha-hunter filter_kr_5m_composite_market_regime.py:31 의 reclaim_strict 기본값
        assert p.recent_return_min == 0.25
        assert p.recent_return_max == 0.50
        assert p.gap_max == 0.08
        assert p.reclaim_end_hhmm == 1030
        assert p.reclaim_vol_pace_mult == 2.0
        assert p.stop_loss_pct == 3.0
        assert p.take_profit_pct == 10.0
        assert p.trail_activation_pct == 5.0
        assert p.trail_pct == 4.0
        assert p.max_hold_days == 1
        assert p.vol_avg_window == 80
        assert p.max_positions == 1


class TestProtocolCompatibility:
    def test_satisfies_signal_engine_protocol(self):
        from kis_backtest.live.signal.engine import SignalEngine

        engine = LiveReclaimEngine()
        eng: SignalEngine = engine  # noqa: F841
        assert engine.name == "reclaim_strict"
        # callable
        result = engine.candidate_signals(pd.Timestamp("2026-05-11"))
        assert isinstance(result, list)


class TestGapFilter:
    def test_gap_too_large_blocks(self):
        asof = date(2026, 5, 11)
        daily = _daily_with_event(asof, n_days=30)
        # iloc[-1] = asof 당일 (mock), iloc[-2] = event_date-1 (급등일)
        prev_close = float(daily.iloc[-2]["close"])
        # gap_max=0.08 → 0.20 갭이면 cut
        base = prev_close * 1.20
        intraday = _intraday_reclaim_pattern(asof, base)
        engine = LiveReclaimEngine()
        engine.set_data(
            daily_data={"005930": daily}, intraday_data={"005930": intraday}
        )
        signals = engine.candidate_signals(asof)
        assert signals == []  # gap 초과로 cut


class TestCustomVariant:
    def test_custom_variant_label(self):
        engine = LiveReclaimEngine(variant="reclaim_loose", priority=3.0)
        assert engine.name == "reclaim_loose"
        assert engine.priority == 3.0


def test_numpy_dependency_present():
    # 코드가 numpy 를 import 하는지 sanity check
    assert np.isfinite(1.0)
