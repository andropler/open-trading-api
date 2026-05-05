"""V4.1 vs 5m Composite 상관관계 분석.

같은 기간(2025-06-20 ~ 2026-04-16, Composite trades 범위)에서
V4.1 디폴트 + V4.1 최적 universe(top10/V3.0/SL7.0) 두 변형의 trades를 추출,
Composite trades CSV와 비교하여:

1. 일별 PnL 시계열 Pearson 상관계수
2. 같은 날짜 매매 빈도 (overlap rate)
3. 같은 ticker 같은 날 매매 빈도
4. 진입 시간 분포 비교
5. regime 미충족일 거동 — V4.1이 Composite이 안 매매하는 날에 얼마나 매매하는지

산출:
    backtester/scripts/validation_v2/correlation_v41_vs_composite.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kis_backtest import BreakoutV41Params, KRIntradayBreakoutV41Backtester


DATA_DIR_1H = "/Users/benjamin/personal_workspace/shared_data/kr_stocks/1h"
KODEX_DAILY = "/Users/benjamin/personal_workspace/shared_data/kr_stocks/daily/069500.KS_1d.parquet"
COMPOSITE_TRADES_CSV = "/Users/benjamin/personal_workspace/open-trading-api/backtester/examples/output/kr_5m_composite_mbull2060/kr_5m_composite_mbull2060_final_trades.csv"

PERIOD_START = "2025-06-20"
PERIOD_END = "2026-04-16"
INITIAL_EQUITY = 10_000_000
MAX_POSITIONS = 3

OUT_PATH = Path(__file__).with_suffix(".json")

V41_VARIANTS = {
    "default": {"breakout_lookback": 4, "vol_multiplier": 2.0, "sl_pct": 5.0, "trail_pct": 0.5, "top_n_stocks": 15, "ranking_window": 5},
    "candidate": {"breakout_lookback": 4, "vol_multiplier": 3.0, "sl_pct": 7.0, "trail_pct": 0.5, "top_n_stocks": 15, "ranking_window": 5},
    "best_universe": {"breakout_lookback": 4, "vol_multiplier": 3.0, "sl_pct": 7.0, "trail_pct": 0.5, "top_n_stocks": 10, "ranking_window": 5},
}


def run_v41(variant_name: str, variant_params: dict, runner: KRIntradayBreakoutV41Backtester | None = None) -> tuple[pd.DataFrame, KRIntradayBreakoutV41Backtester]:
    if runner is None:
        runner = KRIntradayBreakoutV41Backtester(
            data_dir=DATA_DIR_1H,
            params=BreakoutV41Params(**variant_params, cost_pct=0.55),
        )
        runner.load_data()
    else:
        for k, v in variant_params.items():
            setattr(runner.params, k, v)
        runner.params.cost_pct = 0.55

    runner.compute_rankings()
    runner.precompute()
    runner.run(
        initial_equity=INITIAL_EQUITY,
        max_positions=MAX_POSITIONS,
        start_date=PERIOD_START,
        end_date=PERIOD_END,
    )
    df = runner.get_results_df()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["variant"] = variant_name
    return df, runner


def regime_filter(kodex_df: pd.DataFrame) -> pd.DataFrame:
    df = kodex_df.copy()
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma60"] = df["close"].rolling(60).mean()
    df["ret_5d"] = df["close"].pct_change(5)
    df["regime"] = (
        (df["close"].shift(1) > df["sma20"].shift(1))
        & (df["sma20"].shift(1) > df["sma60"].shift(1))
        & (df["ret_5d"].shift(1) > 0)
    )
    return df[["date", "regime"]]


def main() -> None:
    print(f"[CORR] period: {PERIOD_START} ~ {PERIOD_END}")
    composite = pd.read_csv(COMPOSITE_TRADES_CSV)
    composite["entry_date"] = pd.to_datetime(composite["date"])
    composite["entry_dt"] = pd.to_datetime(composite["entry_timestamp"])
    composite["entry_hour"] = composite["entry_dt"].dt.hour
    composite["entry_minute"] = composite["entry_dt"].dt.minute
    composite["ticker"] = composite["ticker"].astype(str).str.zfill(6)
    print(f"[CORR] composite trades: {len(composite)}")

    print(f"[CORR] running V4.1 variants...")
    runner = None
    v41_results: dict[str, pd.DataFrame] = {}
    for name, params in V41_VARIANTS.items():
        df, runner = run_v41(name, params, runner)
        v41_results[name] = df
        print(f"  V4.1 {name:14s}: {len(df)} trades, ret {((df['net_pnl_krw'].sum() / INITIAL_EQUITY)*100):+.1f}%")

    # KODEX regime
    kodex = pd.read_parquet(KODEX_DAILY)
    regime = regime_filter(kodex)
    regime["date"] = pd.to_datetime(regime["date"])
    period_dates = pd.bdate_range(PERIOD_START, PERIOD_END)
    regime_period = regime[regime["date"].isin(period_dates)]
    regime_active_dates = set(regime_period[regime_period["regime"]]["date"].dt.date)
    regime_flat_dates = set(regime_period[~regime_period["regime"]]["date"].dt.date)
    print(f"[CORR] KODEX regime: {len(regime_active_dates)} active days / {len(regime_flat_dates)} flat days")

    # Daily PnL series
    def daily_pnl(df: pd.DataFrame, date_col: str, pnl_col: str) -> pd.Series:
        s = df.groupby(pd.to_datetime(df[date_col]).dt.date)[pnl_col].sum()
        return s

    composite_daily = daily_pnl(composite, "entry_date", "pnl_krw")
    composite_daily.index = pd.to_datetime(composite_daily.index)

    # Build daily index covering both
    all_dates = pd.bdate_range(PERIOD_START, PERIOD_END)
    composite_daily_full = composite_daily.reindex(all_dates).fillna(0.0)

    output: dict[str, Any] = {
        "period": {"start": PERIOD_START, "end": PERIOD_END},
        "composite": {
            "trades": int(len(composite)),
            "tickers": int(composite["ticker"].nunique()),
            "trade_days": int(composite["entry_date"].dt.date.nunique()),
            "total_pnl_krw": float(composite["pnl_krw"].sum()),
            "win_rate": float((composite["pnl_pct"] > 0).mean()),
            "entry_hour_dist": composite.groupby("entry_hour").size().to_dict(),
        },
        "v41_variants": {},
        "comparison": {},
    }

    # V4.1 daily PnL + correlation
    for name, df in v41_results.items():
        v41_daily = daily_pnl(df, "entry_date", "net_pnl_krw")
        v41_daily.index = pd.to_datetime(v41_daily.index)
        v41_daily_full = v41_daily.reindex(all_dates).fillna(0.0)

        # Pearson correlation on daily PnL
        if v41_daily_full.std() > 0 and composite_daily_full.std() > 0:
            corr = float(np.corrcoef(v41_daily_full, composite_daily_full)[0, 1])
        else:
            corr = 0.0

        # day overlap
        v41_dates = set(pd.to_datetime(df["entry_date"]).dt.date)
        comp_dates = set(composite["entry_date"].dt.date)
        intersection = v41_dates & comp_dates
        union = v41_dates | comp_dates
        jaccard = len(intersection) / len(union) if union else 0.0

        # ticker overlap on same day
        v41_d = df.copy()
        v41_d["dt"] = pd.to_datetime(v41_d["entry_date"]).dt.date
        v41_d["ticker"] = v41_d["ticker"].astype(str).str.zfill(6)
        v41_keys = set(zip(v41_d["dt"], v41_d["ticker"]))
        comp_keys = set(zip(composite["entry_date"].dt.date, composite["ticker"]))
        same_day_ticker = v41_keys & comp_keys

        # regime: 비율 of V4.1 trades on regime-flat days
        v41_dates_list = pd.to_datetime(df["entry_date"]).dt.date
        flat_count = sum(1 for d in v41_dates_list if d in regime_flat_dates)
        active_count = sum(1 for d in v41_dates_list if d in regime_active_dates)

        # entry hour distribution (V4.1)
        v41_hours = df["entry_hour"].value_counts().sort_index().to_dict()

        output["v41_variants"][name] = {
            "params": V41_VARIANTS[name],
            "trades": int(len(df)),
            "tickers": int(df["ticker"].nunique()),
            "trade_days": int(len(v41_dates)),
            "total_pnl_krw": float(df["net_pnl_krw"].sum()),
            "win_rate": float((df["net_pnl_krw"] > 0).mean()),
            "entry_hour_dist": {int(k): int(v) for k, v in v41_hours.items()},
            "trades_on_regime_active_days": int(active_count),
            "trades_on_regime_flat_days": int(flat_count),
            "regime_flat_pct": round(flat_count / len(df) * 100, 2) if len(df) else 0.0,
        }

        output["comparison"][name] = {
            "daily_pnl_correlation_pearson": round(corr, 4),
            "trade_days_overlap_jaccard": round(jaccard, 4),
            "trade_days_v41_only": int(len(v41_dates - comp_dates)),
            "trade_days_composite_only": int(len(comp_dates - v41_dates)),
            "trade_days_both": int(len(intersection)),
            "same_day_same_ticker_count": int(len(same_day_ticker)),
            "same_day_same_ticker_examples": [
                {"date": str(d), "ticker": t} for d, t in sorted(same_day_ticker)[:5]
            ],
        }

        print(f"\n=== V4.1 {name} vs Composite ===")
        print(f"  trades: V4.1 {len(df)} / Composite {len(composite)}")
        print(f"  trade days: V4.1 only {len(v41_dates - comp_dates)}, both {len(intersection)}, comp only {len(comp_dates - v41_dates)}")
        print(f"  daily PnL Pearson correlation: {corr:.3f}")
        print(f"  same day + same ticker overlap: {len(same_day_ticker)}")
        print(f"  V4.1 trades on regime FLAT days: {flat_count}/{len(df)} ({flat_count/len(df)*100:.1f}%)")
        print(f"  V4.1 entry hour dist: {v41_hours}")

    print(f"\n=== Composite stats ===")
    print(f"  trades: {len(composite)}, unique tickers: {composite['ticker'].nunique()}, trade days: {composite['entry_date'].dt.date.nunique()}")
    print(f"  entry hour dist: {composite.groupby('entry_hour').size().to_dict()}")

    OUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n[CORR] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
