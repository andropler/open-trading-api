"""F1 — V4.1 권장 후보(L4 V3.0 SL7.0 T0.5) cost stress.

P3에서 발견한 후보 파라미터를 cost ∈ {0.30, 0.55, 0.80, 1.00, 1.50}로
TRAIN/TEST 모두 stress test. 디폴트(P1)와 비교 표 생성.

산출:
    backtester/scripts/validation_v2/f1_v41_candidate_cost_stress.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kis_backtest import BreakoutV41Params, KRIntradayBreakoutV41Backtester


DATA_DIR = "/Users/benjamin/personal_workspace/shared_data/kr_stocks/1h"
TRAIN_START = "2023-03-01"
TRAIN_END = "2024-12-31"
TEST_START = "2025-01-01"
TEST_END = "2026-03-31"
COSTS = [0.30, 0.55, 0.80, 1.00, 1.50]
INITIAL_EQUITY = 10_000_000
MAX_POSITIONS = 3
OUT_PATH = Path(__file__).with_suffix(".json")

# 권장 후보: P3 §3.4
CANDIDATE = {
    "breakout_lookback": 4,
    "vol_multiplier": 3.0,
    "sl_pct": 7.0,
    "trail_pct": 0.5,
}


def _row(cost: float, runner: KRIntradayBreakoutV41Backtester, start: str, end: str) -> dict[str, Any]:
    runner.params.cost_pct = cost
    runner.run(initial_equity=INITIAL_EQUITY, max_positions=MAX_POSITIONS, start_date=start, end_date=end)
    result = runner.to_backtest_result(start_date=start, end_date=end, initial_equity=INITIAL_EQUITY)
    pf = result.profit_factor
    return {
        "cost_pct": cost,
        "trades": int(result.total_trades),
        "win_rate_pct": round(result.win_rate * 100, 2),
        "total_return_pct": round(result.total_return_pct * 100, 2),
        "max_drawdown_pct": round(result.max_drawdown * 100, 2),
        "profit_factor": (round(pf, 4) if pf != float("inf") else None),
        "sharpe_ratio": round(result.sharpe_ratio, 3),
        "cagr_pct": round(result.cagr * 100, 2),
    }


def main() -> None:
    params = BreakoutV41Params(
        breakout_lookback=CANDIDATE["breakout_lookback"],
        vol_multiplier=CANDIDATE["vol_multiplier"],
        sl_pct=CANDIDATE["sl_pct"],
        trail_pct=CANDIDATE["trail_pct"],
    )
    print(f"[F1] candidate params: {CANDIDATE}")
    print(f"[F1] loading 1H data from {DATA_DIR}")
    runner = KRIntradayBreakoutV41Backtester(data_dir=DATA_DIR, params=params)
    runner.load_data().compute_rankings().precompute()
    print(f"[F1] loaded {len(runner.raw_data)} tickers")

    payload: dict[str, Any] = {
        "candidate_params": CANDIDATE,
        "data_dir": DATA_DIR,
        "train_period": {"start": TRAIN_START, "end": TRAIN_END},
        "test_period": {"start": TEST_START, "end": TEST_END},
        "train_results": [],
        "test_results": [],
    }

    print("[F1] TRAIN cost stress (true OOS)...")
    for cost in COSTS:
        row = _row(cost, runner, TRAIN_START, TRAIN_END)
        payload["train_results"].append(row)
        print(f"  TRAIN cost={cost:.2f}% -> {row}")

    print("[F1] TEST cost stress...")
    for cost in COSTS:
        row = _row(cost, runner, TEST_START, TEST_END)
        payload["test_results"].append(row)
        print(f"  TEST  cost={cost:.2f}% -> {row}")

    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[F1] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
