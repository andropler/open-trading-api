"""P1 — V4.1 진짜 OOS(2023-03~2024-12) 비용 stress.

VALIDATION.md §2의 TEST(2025-01~2026-03) 비용 stress와 같은 포맷의 표를
TRAIN 구간(=alpha-hunter fit이 닿지 않은 진짜 OOS)에서 산출한다.

산출:
    backtester/scripts/validation_v2/p1_v41_train_cost_stress.json
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


def _row(cost: float, runner: KRIntradayBreakoutV41Backtester, start: str, end: str) -> dict[str, Any]:
    runner.params.cost_pct = cost
    runner.run(
        initial_equity=INITIAL_EQUITY,
        max_positions=MAX_POSITIONS,
        start_date=start,
        end_date=end,
    )
    result = runner.to_backtest_result(
        start_date=start,
        end_date=end,
        initial_equity=INITIAL_EQUITY,
    )
    pf = result.profit_factor
    return {
        "cost_pct": cost,
        "trades": int(result.total_trades),
        "win_rate_pct": round(result.win_rate * 100, 2),
        "total_return_pct": round(result.total_return_pct * 100, 2),
        "max_drawdown_pct": round(result.max_drawdown * 100, 2),
        "profit_factor": (round(pf, 3) if pf != float("inf") else None),
        "sharpe_ratio": round(result.sharpe_ratio, 3),
        "cagr_pct": round(result.cagr * 100, 2),
    }


def main() -> None:
    print(f"[P1] loading 1H data from {DATA_DIR}")
    runner = KRIntradayBreakoutV41Backtester(data_dir=DATA_DIR, params=BreakoutV41Params())
    runner.load_data().compute_rankings().precompute()
    print(f"[P1] loaded {len(runner.raw_data)} tickers, {len(runner.daily_ranked)} ranked days")

    payload: dict[str, Any] = {
        "data_dir": DATA_DIR,
        "params_default": {
            "breakout_lookback": runner.params.breakout_lookback,
            "vol_multiplier": runner.params.vol_multiplier,
            "trail_pct": runner.params.trail_pct,
            "sl_pct": runner.params.sl_pct,
            "top_n_stocks": runner.params.top_n_stocks,
        },
        "initial_equity": INITIAL_EQUITY,
        "max_positions": MAX_POSITIONS,
        "train_period": {"start": TRAIN_START, "end": TRAIN_END},
        "test_period": {"start": TEST_START, "end": TEST_END},
        "train_results": [],
        "test_results": [],
    }

    print("[P1] running TRAIN cost stress (true OOS)...")
    for cost in COSTS:
        row = _row(cost, runner, TRAIN_START, TRAIN_END)
        payload["train_results"].append(row)
        print(f"  TRAIN cost={cost:.2f}% -> {row}")

    print("[P1] running TEST cost stress (in-sample fit window)...")
    for cost in COSTS:
        row = _row(cost, runner, TEST_START, TEST_END)
        payload["test_results"].append(row)
        print(f"  TEST  cost={cost:.2f}% -> {row}")

    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[P1] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
