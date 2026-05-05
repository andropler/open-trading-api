"""P2 — Composite m_bull_20_60 regime 필터의 약세장 OOS (daily 근사).

5m 데이터가 2025-04부터만 존재하므로 5m 신호 자체는 약세장에서 검증 불가.
그러나 regime 필터(m_bull_20_60: 전일 close>SMA20 AND SMA20>SMA60 AND 5d_return>0)
의 "관망 결정 능력"은 KODEX 200(069500) daily 데이터로 직접 검증 가능.

검증 항목:
    1. 2018-01~2022-12 기간 매매일 / 관망일 분포 (전체+연도별)
    2. 알려진 약세 구간(2018 Q4 폭락, 2020 코로나, 2022 약세장)별 관망률
    3. proxy 백테스트: 069500을 m_bull_20_60에 따라 long/cash 스위칭 → return·MDD
    4. Buy & Hold 069500과 비교 → regime 필터의 부가가치 측정

산출:
    backtester/scripts/validation_v2/p2_composite_regime_bear_oos.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

KODEX_PATH = "/Users/benjamin/personal_workspace/shared_data/kr_stocks/daily/069500.KS_1d.parquet"
PERIOD_START = "2018-01-01"
PERIOD_END = "2022-12-31"

# 알려진 약세 구간 (KOSPI/KOSPI200 drawdown windows)
BEAR_WINDOWS: list[tuple[str, str, str]] = [
    ("2018_q4_crash", "2018-10-01", "2018-12-31"),
    ("2020_covid", "2020-02-15", "2020-04-15"),
    ("2022_bear", "2022-01-01", "2022-12-31"),
]

# 비교용 강세 구간 (regime이 매매를 허용해야 하는 시기)
BULL_WINDOWS: list[tuple[str, str, str]] = [
    ("2019_recovery", "2019-01-01", "2019-12-31"),
    ("2020_h2_recovery", "2020-04-15", "2020-12-31"),
    ("2021_rally", "2021-01-01", "2021-06-30"),
]

OUT_PATH = Path(__file__).with_suffix(".json")


def metrics(returns: pd.Series) -> dict[str, float]:
    """Compute total return, Sharpe, MDD on a daily return series."""
    r = returns.fillna(0.0)
    if r.empty:
        return {"total_return_pct": 0.0, "sharpe": 0.0, "mdd_pct": 0.0, "vol_ann_pct": 0.0}
    equity = (1 + r).cumprod()
    total_return = float(equity.iloc[-1] - 1.0) * 100.0
    if r.std() > 0:
        sharpe = float(r.mean() / r.std() * np.sqrt(252))
    else:
        sharpe = 0.0
    rolling_max = equity.cummax()
    mdd = float(((equity - rolling_max) / rolling_max).min()) * 100.0
    vol_ann = float(r.std() * np.sqrt(252)) * 100.0
    return {
        "total_return_pct": round(total_return, 2),
        "sharpe": round(sharpe, 3),
        "mdd_pct": round(mdd, 2),
        "vol_ann_pct": round(vol_ann, 2),
    }


def main() -> None:
    print(f"[P2] loading {KODEX_PATH}")
    df = pd.read_parquet(KODEX_PATH)
    df = df.copy()
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)

    # SMA + 5d return
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma60"] = df["close"].rolling(60).mean()
    df["ret_5d"] = df["close"].pct_change(5)

    # regime: previous-day values (no look-ahead).
    # Decision is made before market open using yesterday's close/SMA/5d return.
    df["regime"] = (
        (df["close"].shift(1) > df["sma20"].shift(1))
        & (df["sma20"].shift(1) > df["sma60"].shift(1))
        & (df["ret_5d"].shift(1) > 0)
    )

    df["daily_ret"] = df["close"].pct_change()
    # strategy daily return: today's KODEX return only when regime allowed
    df["strat_ret"] = df["daily_ret"].where(df["regime"], 0.0)

    period_mask = (df["date"] >= pd.Timestamp(PERIOD_START).date()) & (
        df["date"] <= pd.Timestamp(PERIOD_END).date()
    )
    period = df.loc[period_mask].reset_index(drop=True)

    total_days = len(period)
    trade_days = int(period["regime"].sum())
    flat_days = total_days - trade_days

    # yearly breakdown
    period_with_year = period.copy()
    period_with_year["year"] = pd.to_datetime(period_with_year["date"]).dt.year
    yearly_rows: list[dict[str, Any]] = []
    for year, grp in period_with_year.groupby("year"):
        n = len(grp)
        td = int(grp["regime"].sum())
        bh = metrics(grp["daily_ret"])
        st = metrics(grp["strat_ret"])
        yearly_rows.append(
            {
                "year": int(year),
                "total_days": n,
                "trade_days": td,
                "flat_days": n - td,
                "flat_pct": round((n - td) / n * 100, 2) if n else 0.0,
                "bh_return_pct": bh["total_return_pct"],
                "strat_return_pct": st["total_return_pct"],
                "bh_mdd_pct": bh["mdd_pct"],
                "strat_mdd_pct": st["mdd_pct"],
            }
        )

    def window_stats(label: str, start: str, end: str) -> dict[str, Any]:
        mask = (df["date"] >= pd.Timestamp(start).date()) & (df["date"] <= pd.Timestamp(end).date())
        sub = df.loc[mask].reset_index(drop=True)
        n = len(sub)
        td = int(sub["regime"].sum())
        bh = metrics(sub["daily_ret"])
        st = metrics(sub["strat_ret"])
        return {
            "label": label,
            "start": start,
            "end": end,
            "total_days": n,
            "trade_days": td,
            "flat_days": n - td,
            "flat_pct": round((n - td) / n * 100, 2) if n else 0.0,
            "bh_return_pct": bh["total_return_pct"],
            "bh_mdd_pct": bh["mdd_pct"],
            "strat_return_pct": st["total_return_pct"],
            "strat_mdd_pct": st["mdd_pct"],
            "regime_avoided_dd_pct": round(bh["mdd_pct"] - st["mdd_pct"], 2),
        }

    bear_results = [window_stats(label, s, e) for label, s, e in BEAR_WINDOWS]
    bull_results = [window_stats(label, s, e) for label, s, e in BULL_WINDOWS]

    # full period proxy backtest
    bh_full = metrics(period["daily_ret"])
    st_full = metrics(period["strat_ret"])

    payload = {
        "data_path": KODEX_PATH,
        "period": {"start": PERIOD_START, "end": PERIOD_END},
        "rule": "m_bull_20_60: y_close > y_SMA20 AND y_SMA20 > y_SMA60 AND y_5d_return > 0 (yesterday-only)",
        "summary": {
            "total_days": total_days,
            "trade_days": trade_days,
            "flat_days": flat_days,
            "flat_pct": round(flat_days / total_days * 100, 2) if total_days else 0.0,
            "bh_return_pct": bh_full["total_return_pct"],
            "bh_mdd_pct": bh_full["mdd_pct"],
            "bh_sharpe": bh_full["sharpe"],
            "strat_return_pct": st_full["total_return_pct"],
            "strat_mdd_pct": st_full["mdd_pct"],
            "strat_sharpe": st_full["sharpe"],
            "regime_avoided_dd_pct": round(bh_full["mdd_pct"] - st_full["mdd_pct"], 2),
        },
        "yearly": yearly_rows,
        "bear_windows": bear_results,
        "bull_windows": bull_results,
    }

    print("[P2] summary:")
    print(json.dumps(payload["summary"], indent=2, ensure_ascii=False))
    print("[P2] bear windows:")
    for row in bear_results:
        print(f"  {row['label']:20s} flat={row['flat_pct']:5.1f}% B&H={row['bh_return_pct']:+6.1f}% (MDD {row['bh_mdd_pct']:+6.1f}%) | strat={row['strat_return_pct']:+6.1f}% (MDD {row['strat_mdd_pct']:+6.1f}%)")
    print("[P2] bull windows:")
    for row in bull_results:
        print(f"  {row['label']:20s} flat={row['flat_pct']:5.1f}% B&H={row['bh_return_pct']:+6.1f}% (MDD {row['bh_mdd_pct']:+6.1f}%) | strat={row['strat_return_pct']:+6.1f}% (MDD {row['strat_mdd_pct']:+6.1f}%)")

    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[P2] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
