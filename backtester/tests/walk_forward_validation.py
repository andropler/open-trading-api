#!/usr/bin/env python3
"""Walk-forward 시간 분할 검증.

그리드-best 파라미터를 2 구간에 각각 적용해 시간 robustness 평가:
    A. in-sample : 2018-01-01 ~ 2022-12-31 (5년) — 강세/약세/횡보 모두 포함
    B. out-of-sample : 2023-01-01 ~ 2026-03-25 (3.3년) — 최근 강세

판정:
    - |ΔSharpe| < 0.3 → ROBUST
    - in-sample 우월 (S↑) and OOS 열등 (S↓) → 시기 의존성
    - PF/MDD 일관성도 함께 체크

200종 universe는 동일 (Phase 1 / Phase 1.5와 일관).
"""

from __future__ import annotations

import csv
import glob
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from kis_backtest import LeanClient
from kis_backtest.models import Resolution
from kis_backtest.providers import ParquetDataProvider
from kis_backtest.utils.universe import top_n_by_turnover

logger = logging.getLogger("walkfwd")
SWEEP_DIR = REPO_ROOT / "examples" / "output" / "sweep"

WINDOWS = [
    ("in_sample", "2018-01-01", "2022-12-31"),
    ("out_of_sample", "2023-01-01", "2026-03-25"),
]

# consecutive_moves는 이미 폐기 확정이라 제외. 7개만 검증.
EXCLUDE_STRATEGIES = {"consecutive_moves"}


def best_grid(grid_csv: Path, min_trades: int = 100) -> Dict[str, Dict[str, Any]]:
    df = pd.read_csv(grid_csv)
    df = df[df["success"] == True].copy()
    df["sharpe_ratio"] = pd.to_numeric(df["sharpe_ratio"], errors="coerce")
    df["total_trades"] = pd.to_numeric(df["total_trades"], errors="coerce")
    df = df[df["total_trades"] >= min_trades].dropna(subset=["sharpe_ratio"])
    idx = df.groupby("strategy_id")["sharpe_ratio"].idxmax()
    best = df.loc[idx]
    out: Dict[str, Dict[str, Any]] = {}
    for _, row in best.iterrows():
        out[row["strategy_id"]] = {
            "param_name1": row["param_name1"],
            "param_value1": row["param_value1"],
            "param_name2": row["param_name2"],
            "param_value2": row["param_value2"],
        }
    return out


def coerce_param(name: str, value: float) -> Any:
    int_params = {
        "fast_period", "slow_period", "lookback", "stop_loss_pct",
        "up_days", "down_days", "period", "exit_days",
        "atr_period", "trend_period",
    }
    float_params = {"threshold", "threshold_pct", "buy_ratio", "min_close_ratio"}
    if name in float_params:
        return float(value)
    if name in int_params:
        return int(value)
    return float(value)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    grid_files = sorted(SWEEP_DIR.glob("grid_top*_*.csv"))
    if not grid_files:
        raise SystemExit("No grid CSV")
    grid_csv = grid_files[-1]
    best = best_grid(grid_csv)
    logger.info("Grid: %s, %d strategies", grid_csv.name, len(best))

    provider = ParquetDataProvider()
    client = LeanClient(data_provider=provider)
    symbols = top_n_by_turnover(
        provider, n=200, resolution=Resolution.DAILY,
        lookback_start=datetime(2018, 1, 1).date(),
        lookback_end=datetime(2026, 3, 25).date(),
        exclude_etfs=True,
    )
    logger.info("Universe: %d symbols", len(symbols))

    rows: List[Dict[str, Any]] = []
    out_path = SWEEP_DIR / f"walk_forward_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    for sid, info in best.items():
        if sid in EXCLUDE_STRATEGIES:
            continue
        params = {
            info["param_name1"]: coerce_param(info["param_name1"], info["param_value1"]),
            info["param_name2"]: coerce_param(info["param_name2"], info["param_value2"]),
        }
        logger.info("[%s] params=%s", sid, params)

        for window_label, start, end in WINDOWS:
            t0 = time.time()
            try:
                r = client.backtest_strategy(
                    strategy_id=sid, symbols=symbols,
                    start_date=start, end_date=end,
                    params=params, initial_cash=100_000_000,
                )
                elapsed = time.time() - t0
                rows.append({
                    "strategy_id": sid,
                    "window": window_label,
                    "start_date": start, "end_date": end,
                    "param1": f"{info['param_name1']}={params[info['param_name1']]}",
                    "param2": f"{info['param_name2']}={params[info['param_name2']]}",
                    "sharpe_ratio": r.sharpe_ratio,
                    "profit_factor": r.profit_factor,
                    "total_return_pct": round(r.total_return_pct, 6),
                    "cagr": round(r.cagr, 6),
                    "max_drawdown": round(r.max_drawdown, 6),
                    "total_trades": r.total_trades,
                    "win_rate": round(r.win_rate, 6),
                    "duration_seconds": round(elapsed, 1),
                    "success": True, "error": "",
                })
                logger.info("  [%s] ret=%+.1f%% sharpe=%.2f PF=%.2f MDD=%.1f%% trades=%d (%.0fs)",
                            window_label, r.total_return_pct*100, r.sharpe_ratio, r.profit_factor,
                            r.max_drawdown*100, r.total_trades, elapsed)
            except Exception as e:
                logger.exception("  [%s] FAIL: %s", window_label, e)
                rows.append({
                    "strategy_id": sid, "window": window_label,
                    "start_date": start, "end_date": end,
                    "param1": "", "param2": "",
                    "sharpe_ratio": "", "profit_factor": "",
                    "total_return_pct": "", "cagr": "", "max_drawdown": "",
                    "total_trades": "", "win_rate": "",
                    "duration_seconds": "", "success": False, "error": f"{type(e).__name__}: {e}",
                })
            with out_path.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)

    logger.info("DONE: %s", out_path)


if __name__ == "__main__":
    main()
