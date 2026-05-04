#!/usr/bin/env python3
"""그리드 최적 파라미터를 더 큰 유니버스(200종) × 8년에 적용해 robust 검증.

흐름:
    1. 최신 grid_top50_*.csv 읽음
    2. 각 전략별 sharpe_ratio가 최대인 그리드 포인트 (trades>=100) 선택
    3. 그 파라미터로 200종목 × 8년 백테스트
    4. 디폴트(200종) vs 그리드(50종) vs 그리드-on-200(out-of-sample size) 비교 CSV/markdown
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

logger = logging.getLogger("validate")

SWEEP_DIR = REPO_ROOT / "examples" / "output" / "sweep"


def best_grid(grid_csv: Path, min_trades: int = 100) -> Dict[str, Dict[str, Any]]:
    df = pd.read_csv(grid_csv)
    df = df[df["success"] == True].copy()
    df["sharpe_ratio"] = pd.to_numeric(df["sharpe_ratio"], errors="coerce")
    df["total_trades"] = pd.to_numeric(df["total_trades"], errors="coerce")
    df = df[df["total_trades"] >= min_trades]
    df = df.dropna(subset=["sharpe_ratio"])
    if df.empty:
        return {}
    idx = df.groupby("strategy_id")["sharpe_ratio"].idxmax()
    best = df.loc[idx]
    out = {}
    for _, row in best.iterrows():
        out[row["strategy_id"]] = {
            "param_name1": row["param_name1"],
            "param_value1": row["param_value1"],
            "param_name2": row["param_name2"],
            "param_value2": row["param_value2"],
            "grid_sharpe": float(row["sharpe_ratio"]),
            "grid_pf": float(pd.to_numeric(row["profit_factor"], errors="coerce")),
            "grid_ret_pct": float(pd.to_numeric(row["total_return_pct"], errors="coerce")),
        }
    return out


def coerce_param(name: str, value: float) -> Any:
    """그리드 CSV에는 모두 float로 저장됨 — int 파라미터를 캐스팅."""
    int_params = {
        "fast_period", "slow_period", "lookback", "stop_loss_pct",
        "up_days", "down_days", "period", "exit_days",
        "atr_period", "trend_period",
    }
    # threshold류는 float 유지 (threshold, threshold_pct, sl_pct, etc.)
    float_params = {
        "threshold", "threshold_pct", "buy_ratio", "min_close_ratio",
    }
    if name in float_params:
        return float(value)
    if name in int_params:
        return int(value)
    return float(value)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    grid_files = sorted(SWEEP_DIR.glob("grid_top*_*.csv"))
    if not grid_files:
        raise SystemExit("No grid CSV found")
    grid_csv = grid_files[-1]
    logger.info("Using grid: %s", grid_csv.name)

    best = best_grid(grid_csv)
    logger.info("%d strategies have grid-best params: %s", len(best), list(best.keys()))

    # 200 universe (Phase 1과 동일)
    provider = ParquetDataProvider()
    client = LeanClient(data_provider=provider)
    symbols = top_n_by_turnover(
        provider, n=200, resolution=Resolution.DAILY,
        lookback_start=datetime(2018,1,1).date(),
        lookback_end=datetime(2026,3,25).date(),
        exclude_etfs=True,
    )
    logger.info("Universe: top 200 daily")

    rows: List[Dict[str, Any]] = []
    out_path = SWEEP_DIR / f"grid_best_validation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    for sid, info in best.items():
        params = {
            info["param_name1"]: coerce_param(info["param_name1"], info["param_value1"]),
            info["param_name2"]: coerce_param(info["param_name2"], info["param_value2"]),
        }
        logger.info("[%s] params=%s (grid: S=%.2f, PF=%.2f, ret=%+.1f%%)",
                    sid, params, info["grid_sharpe"], info["grid_pf"], info["grid_ret_pct"]*100)

        t0 = time.time()
        try:
            r = client.backtest_strategy(
                strategy_id=sid, symbols=symbols,
                start_date="2018-01-01", end_date="2026-03-25",
                params=params, initial_cash=100_000_000,
            )
            elapsed = time.time() - t0
            row = {
                "strategy_id": sid,
                "param_name1": info["param_name1"], "param_value1": params[info["param_name1"]],
                "param_name2": info["param_name2"], "param_value2": params[info["param_name2"]],
                "grid50_sharpe": info["grid_sharpe"],
                "grid50_pf": info["grid_pf"],
                "grid50_ret_pct": info["grid_ret_pct"],
                "validate200_sharpe": r.sharpe_ratio,
                "validate200_pf": r.profit_factor,
                "validate200_ret_pct": round(r.total_return_pct, 6),
                "validate200_mdd": round(r.max_drawdown, 6),
                "validate200_trades": r.total_trades,
                "validate200_winrate": round(r.win_rate, 6),
                "duration_seconds": round(elapsed, 1),
                "success": True, "error": "",
            }
            logger.info("  → 200-universe: ret=%+.1f%% sharpe=%.2f PF=%.2f MDD=%.1f%% trades=%d (%.0fs)",
                        r.total_return_pct*100, r.sharpe_ratio, r.profit_factor, r.max_drawdown*100,
                        r.total_trades, elapsed)
        except Exception as e:
            logger.exception("  FAILED: %s", e)
            row = {
                "strategy_id": sid,
                "param_name1": info["param_name1"], "param_value1": params.get(info["param_name1"]),
                "param_name2": info["param_name2"], "param_value2": params.get(info["param_name2"]),
                "grid50_sharpe": info["grid_sharpe"],
                "grid50_pf": info["grid_pf"],
                "grid50_ret_pct": info["grid_ret_pct"],
                "validate200_sharpe": "", "validate200_pf": "", "validate200_ret_pct": "",
                "validate200_mdd": "", "validate200_trades": "", "validate200_winrate": "",
                "duration_seconds": "", "success": False, "error": f"{type(e).__name__}: {e}",
            }
        rows.append(row)
        with out_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    logger.info("DONE: %s", out_path)


if __name__ == "__main__":
    main()
