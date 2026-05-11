"""LiveReclaimEngine — alpha-hunter IntradayReclaimEngine 의 신호 생성부 자체 포팅.

원본: alpha-hunter/src/backtest/kr_intraday_reclaim_engine.py
포팅 범위:
- IntradayReclaimParams (37 필드, 동일 의미)
- compute_candidates: 일봉 기반 후보 종목 (event_return + recent_return + 거래대금)
- precompute: 5m 봉 → VWAP + intraday_prior_high 추가
- confirmation_signals: pullback → reclaim 패턴 5m bar 매칭

라이브 어댑터:
- daily_data: dict[ticker → pd.DataFrame(timestamp, open, high, low, close, volume)] — 외부 주입
- intraday_data: dict[ticker → pd.DataFrame(timestamp, open, high, low, close, volume)] — 매일 5m bar buffer 에서 빌드
- SignalEngine Protocol 의 candidate_signals(asof_date) → list[Signal] 반환

alpha-hunter import 0 — pandas + numpy + 표준 라이브러리만 사용.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date as _date
from typing import Optional

import numpy as np
import pandas as pd

from kis_backtest.live.signal.models import ExitProfile, Signal


@dataclass(frozen=True)
class IntradayReclaimParams:
    """alpha-hunter kr_intraday_reclaim_engine.py:22-58 와 동치.

    reclaim_strict 변종 기본값(researchKR.../filter_kr_5m_composite_market_regime.py:31):
    candidate_mode='recent', recent_return_min=0.25, recent_return_max=0.50,
    gap_max=0.08, reclaim_end_hhmm=1030, reclaim_vol_pace_mult=2.0,
    stop_loss_pct=3.0, take_profit_pct=10.0, trail_activation_pct=5.0,
    trail_pct=4.0, max_hold_days=1, vol_avg_window=80.
    """

    interval: str = "5m"
    event_return_min: float = 0.15
    event_return_max: float = 0.30
    recent_return_window: int = 3
    recent_return_min: float = 0.25
    recent_return_max: Optional[float] = 0.50
    candidate_mode: str = "recent"  # event | recent | both | either
    min_price: float = 5_000
    min_event_trading_value: float = 10_000_000_000
    event_volume_mult: float = 2.0
    event_volume_avg_window: int = 20
    gap_min: float = 0.0
    gap_max: float = 0.08
    pullback_start_hhmm: int = 900
    pullback_end_hhmm: int = 1030
    reclaim_start_hhmm: int = 930
    reclaim_end_hhmm: int = 1030
    pullback_vwap_tolerance: float = 0.003
    require_close_below_vwap: bool = True
    require_bullish_bar: bool = True
    vol_avg_window: int = 80
    reclaim_vol_pace_mult: float = 2.0
    require_intraday_high_reclaim: bool = False
    stop_loss_pct: float = 3.0
    stop_buffer_pct: float = 0.5
    take_profit_pct: float = 10.0
    trail_activation_pct: float = 5.0
    trail_pct: float = 4.0
    max_hold_days: int = 1
    exit_hhmm: int = 1430
    weakness_exit_enabled: bool = True
    weakness_exit_hhmm: int = 1430
    max_positions: int = 1


def _hhmm_series(ts: pd.Series) -> pd.Series:
    return ts.dt.hour * 100 + ts.dt.minute


def _compute_candidates(
    daily_data: dict[str, pd.DataFrame],
    intraday_data: dict[str, pd.DataFrame],
    params: IntradayReclaimParams,
) -> dict[_date, dict[str, dict]]:
    """일봉 기준 이벤트(급등) → 다음날 후보. alpha-hunter line 124-184 동치."""
    by_date: dict[_date, dict[str, dict]] = {}
    p = params
    for ticker, raw_df in intraday_data.items():
        daily = daily_data.get(ticker)
        if daily is None or daily.empty:
            continue
        d = daily.copy()
        d["timestamp"] = pd.to_datetime(d["timestamp"])
        d["date"] = d["timestamp"].dt.date
        d["trading_value"] = d["close"].astype(float) * d["volume"].astype(float)
        d["event_return"] = d["close"].pct_change()
        d["recent_return"] = (
            d["close"] / d["close"].shift(p.recent_return_window) - 1
        )
        prior_vol = (
            d["volume"]
            .replace(0, np.nan)
            .shift(1)
            .rolling(p.event_volume_avg_window, min_periods=5)
            .mean()
        )
        d["prior_avg_volume"] = prior_vol
        d["event_volume_ratio"] = d["volume"] / prior_vol

        event_mask = d["event_return"].between(
            p.event_return_min, p.event_return_max, inclusive="both"
        )
        recent_mask = d["recent_return"] >= p.recent_return_min
        if p.recent_return_max is not None:
            recent_mask &= d["recent_return"] <= p.recent_return_max
        if p.candidate_mode == "event":
            mask = event_mask
        elif p.candidate_mode == "recent":
            mask = recent_mask
        elif p.candidate_mode == "both":
            mask = event_mask & recent_mask
        else:  # either
            mask = event_mask | recent_mask

        events = d[
            mask
            & (d["trading_value"] >= p.min_event_trading_value)
            & (d["close"] >= p.min_price)
            & (d["event_volume_ratio"] >= p.event_volume_mult)
        ]
        if events.empty:
            continue
        event_by_idx = {int(idx): row for idx, row in events.iterrows()}
        daily_dates = d["date"].tolist()
        daily_date_set = set(daily_dates)
        raw_dates = pd.to_datetime(raw_df["timestamp"]).dt.date
        for trade_date in sorted(raw_dates.unique()):
            if trade_date not in daily_date_set:
                continue
            prev_idx = bisect.bisect_left(daily_dates, trade_date) - 1
            if prev_idx not in event_by_idx:
                continue
            row = event_by_idx[prev_idx]
            by_date.setdefault(trade_date, {})[ticker] = {
                "event_date": row["date"],
                "event_return": float(row["event_return"]),
                "recent_return": float(row["recent_return"]),
                "event_close": float(row["close"]),
                "event_high": float(row["high"]),
                "event_trading_value": float(row["trading_value"]),
                "event_volume_ratio": float(row["event_volume_ratio"]),
            }
    return by_date


def _precompute_intraday(
    intraday_data: dict[str, pd.DataFrame], params: IntradayReclaimParams
) -> dict[str, dict[_date, dict]]:
    """5m 봉별 VWAP + avg_volume + intraday_prior_high 부여. alpha-hunter line 186-218 동치."""
    stock_days: dict[str, dict[_date, dict]] = {}
    p = params
    for ticker, raw_df in intraday_data.items():
        df = raw_df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["date"] = df["timestamp"].dt.date
        df["hhmm"] = _hhmm_series(df["timestamp"])
        vol = df["volume"].astype(float).replace(0, np.nan)
        df["avg_volume"] = (
            vol.shift(1).rolling(p.vol_avg_window, min_periods=5).mean()
        )
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_vol"] = df["typical_price"] * df["volume"]
        df["cum_tp_vol"] = df.groupby("date")["tp_vol"].cumsum()
        df["cum_vol"] = df.groupby("date")["volume"].cumsum()
        df["vwap"] = df["cum_tp_vol"] / df["cum_vol"].replace(0, np.nan)

        days: dict[_date, dict] = {}
        for d, grp in df.groupby("date"):
            g = grp.sort_values("timestamp").reset_index(drop=True)
            if len(g) < 5:
                continue
            days[d] = {
                "timestamp": g["timestamp"].values,
                "hhmm": g["hhmm"].values,
                "open": g["open"].astype(float).values,
                "high": g["high"].astype(float).values,
                "low": g["low"].astype(float).values,
                "close": g["close"].astype(float).values,
                "volume": g["volume"].astype(float).values,
                "avg_volume": g["avg_volume"].astype(float).values,
                "vwap": g["vwap"].astype(float).values,
                "intraday_prior_high": g["high"].shift(1).cummax().astype(float).values,
            }
        stock_days[ticker] = days
    return stock_days


def _gap_ok(
    day: dict, meta: dict, params: IntradayReclaimParams
) -> tuple[bool, float]:
    if meta["event_close"] <= 0 or len(day["open"]) == 0:
        return False, float("nan")
    gap = float(day["open"][0] / meta["event_close"] - 1)
    return params.gap_min <= gap <= params.gap_max, gap


def _confirmation_signals_for_date(
    asof_date: _date,
    candidates: dict[_date, dict[str, dict]],
    stock_days: dict[str, dict[_date, dict]],
    intraday_data: dict[str, pd.DataFrame],
    params: IntradayReclaimParams,
) -> list[dict]:
    """asof_date 의 reclaim 진입 신호 생성. alpha-hunter line 234-318 의 핵심 루프 동치.

    asof_date 의 후보 종목들에 대해서만 5m bar 를 순회하며 진입 조건 평가.
    """
    p = params
    ticker_map = candidates.get(asof_date, {})
    out: list[dict] = []
    for ticker, meta in ticker_map.items():
        day = stock_days.get(ticker, {}).get(asof_date)
        if not day:
            continue
        gap_pass, gap_pct = _gap_ok(day, meta, params)
        if not gap_pass:
            continue
        raw_df = intraday_data[ticker]
        timestamps = pd.to_datetime(raw_df["timestamp"]).values
        for idx in range(len(day["hhmm"])):
            hhmm = int(day["hhmm"][idx])
            if hhmm < p.reclaim_start_hhmm or hhmm > p.reclaim_end_hhmm:
                continue
            close = float(day["close"][idx])
            open_ = float(day["open"][idx])
            high = float(day["high"][idx])
            vol = float(day["volume"][idx])
            avg_vol = float(day["avg_volume"][idx])
            vwap = float(day["vwap"][idx])
            if np.isnan(vwap) or np.isnan(avg_vol) or avg_vol <= 0 or vol <= 0:
                continue
            if close < p.min_price or close <= vwap:
                continue
            if p.require_bullish_bar and close <= open_:
                continue
            if vol < p.reclaim_vol_pace_mult * avg_vol:
                continue
            if p.require_intraday_high_reclaim:
                prior_high = float(day["intraday_prior_high"][idx])
                if np.isnan(prior_high) or high <= prior_high:
                    continue
            prev_idx = idx - 1
            if prev_idx >= 0 and float(day["close"][prev_idx]) > float(
                day["vwap"][prev_idx]
            ) * (1 + p.pullback_vwap_tolerance):
                continue

            pull_idxs = [
                j
                for j in range(idx)
                if p.pullback_start_hhmm <= int(day["hhmm"][j]) <= p.pullback_end_hhmm
                and not np.isnan(float(day["vwap"][j]))
                and float(day["low"][j])
                <= float(day["vwap"][j]) * (1 + p.pullback_vwap_tolerance)
            ]
            if not pull_idxs:
                continue
            if p.require_close_below_vwap and not any(
                float(day["close"][j]) < float(day["vwap"][j]) for j in pull_idxs
            ):
                continue

            ts = day["timestamp"][idx]
            next_idx = int(np.searchsorted(timestamps, ts, side="right"))
            if next_idx >= len(raw_df):
                continue
            next_date = pd.Timestamp(timestamps[next_idx]).date()
            if next_date != asof_date:
                continue
            entry = float(raw_df["open"].values[next_idx])
            if entry <= 0 or np.isnan(entry):
                continue
            pullback_low = float(np.nanmin([day["low"][j] for j in pull_idxs]))
            fixed_stop = entry * (1 - p.stop_loss_pct / 100)
            pull_stop = pullback_low * (1 - p.stop_buffer_pct / 100)
            stop_price = max(fixed_stop, pull_stop)
            if stop_price >= entry:
                continue
            entry_ts = pd.Timestamp(timestamps[next_idx])
            entry_hhmm = entry_ts.hour * 100 + entry_ts.minute
            out.append(
                {
                    "ticker": ticker,
                    "entry_ts": entry_ts,
                    "entry_hhmm": int(entry_hhmm),
                    "entry_price": entry,
                    "stop_price": stop_price,
                    "confirm_vol_ratio": vol / avg_vol,
                    "gap_pct": gap_pct,
                    "event_meta": meta,
                }
            )
    out.sort(key=lambda r: (r["entry_hhmm"], r["ticker"]))
    return out


class LiveReclaimEngine:
    """SignalEngine Protocol 구현체 — reclaim_strict variant 단일 엔진.

    사용:
        engine = LiveReclaimEngine(params=IntradayReclaimParams(...))
        engine.set_data(daily_data, intraday_data)
        signals = engine.candidate_signals(asof_date)
    """

    def __init__(
        self,
        params: IntradayReclaimParams | None = None,
        *,
        variant: str = "reclaim_strict",
        priority: float = 5.0,
    ):
        self.params = params or IntradayReclaimParams()
        self.variant = variant
        self.priority = priority
        self._daily: dict[str, pd.DataFrame] = {}
        self._intraday: dict[str, pd.DataFrame] = {}
        self._cached_asof: _date | None = None
        self._cached_candidates: dict[_date, dict[str, dict]] = {}
        self._cached_stock_days: dict[str, dict[_date, dict]] = {}

    @property
    def name(self) -> str:
        return self.variant

    def set_data(
        self,
        daily_data: dict[str, pd.DataFrame],
        intraday_data: dict[str, pd.DataFrame],
    ) -> None:
        """매일 장 시작 전 일봉(과거)과 intraday(누적된 5m bar)를 주입."""
        self._daily = daily_data
        self._intraday = intraday_data
        # cache invalidate
        self._cached_asof = None
        self._cached_candidates = {}
        self._cached_stock_days = {}

    def _ensure_precompute(self) -> None:
        if not self._cached_candidates:
            self._cached_candidates = _compute_candidates(
                self._daily, self._intraday, self.params
            )
        if not self._cached_stock_days:
            self._cached_stock_days = _precompute_intraday(
                self._intraday, self.params
            )

    def _exit_profile(self) -> ExitProfile:
        return ExitProfile(
            stop_loss_pct=self.params.stop_loss_pct,
            take_profit_pct=self.params.take_profit_pct,
            trail_activation_pct=self.params.trail_activation_pct,
            trail_pct=self.params.trail_pct,
            max_hold_days=self.params.max_hold_days,
            exit_hhmm=self.params.exit_hhmm,
            weakness_exit_enabled=self.params.weakness_exit_enabled,
            weakness_exit_hhmm=self.params.weakness_exit_hhmm,
        )

    def candidate_signals(self, asof_date) -> list[Signal]:
        if not self._daily or not self._intraday:
            return []
        target = pd.Timestamp(asof_date).date()
        self._ensure_precompute()
        raw_signals = _confirmation_signals_for_date(
            target,
            self._cached_candidates,
            self._cached_stock_days,
            self._intraday,
            self.params,
        )
        profile = self._exit_profile()
        signals: list[Signal] = []
        for raw in raw_signals:
            sig = Signal.from_raw(
                source="reclaim",
                variant=self.variant,
                ticker=raw["ticker"],
                entry_ts=raw["entry_ts"],
                entry_price=raw["entry_price"],
                profile=profile,
                priority=self.priority,
                explicit_stop=raw["stop_price"],
                score=raw["confirm_vol_ratio"],
            )
            if sig is not None:
                signals.append(sig)
        return signals


__all__ = [
    "IntradayReclaimParams",
    "LiveReclaimEngine",
]
