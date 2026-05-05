"""F2 — V4.1 universe 차원 추가 그리드.

P3는 lookback × vol × sl × trail만 다룸. 본 스크립트는 후보 패턴(V3.0 SL7.0 T0.5)을
고정한 채 universe 차원(top_n × ranking_window)에서도 robust한지 확인.

또한 vol×3.0/sl 7.0이 universe 변경에서도 dominant인지 비교 — 디폴트 universe(top15, rw=5)와
다른 settings에서도 후보가 더 좋을지.

그리드:
    top_n_stocks ∈ {10, 15, 20, 25}
    ranking_window ∈ {3, 5, 7, 10}
    × params: 디폴트(V2.0 SL5.0 T0.5) vs 후보(V3.0 SL7.0 T0.5)

총 16 × 2 = 32 조합 × TRAIN+TEST = 64 run. ~3~5분 예상.

산출:
    backtester/scripts/validation_v2/f2_v41_universe_grid.json
"""

from __future__ import annotations

import json
import sys
import time
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
COST = 0.55
INITIAL_EQUITY = 10_000_000
MAX_POSITIONS = 3

PARAM_VARIANTS = {
    "default": {"vol_multiplier": 2.0, "sl_pct": 5.0, "trail_pct": 0.5},
    "candidate": {"vol_multiplier": 3.0, "sl_pct": 7.0, "trail_pct": 0.5},
}
TOP_N_LIST = [10, 15, 20, 25]
RANKING_WINDOW_LIST = [3, 5, 7, 10]

OUT_PATH = Path(__file__).with_suffix(".json")


def harmonic_mean(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return 2.0 * a * b / (a + b)


def metrics_run(runner: KRIntradayBreakoutV41Backtester, start: str, end: str) -> dict[str, Any]:
    runner.run(initial_equity=INITIAL_EQUITY, max_positions=MAX_POSITIONS, start_date=start, end_date=end)
    res = runner.to_backtest_result(start_date=start, end_date=end, initial_equity=INITIAL_EQUITY)
    pf = res.profit_factor
    return {
        "trades": int(res.total_trades),
        "win_pct": round(res.win_rate * 100, 2),
        "ret_pct": round(res.total_return_pct * 100, 2),
        "mdd_pct": round(res.max_drawdown * 100, 2),
        "pf": (round(pf, 4) if pf != float("inf") else None),
        "sharpe": round(res.sharpe_ratio, 3),
    }


def main() -> None:
    print(f"[F2] loading 1H data from {DATA_DIR}")
    runner = KRIntradayBreakoutV41Backtester(
        data_dir=DATA_DIR,
        params=BreakoutV41Params(cost_pct=COST),
    )
    runner.load_data()
    print(f"[F2] loaded {len(runner.raw_data)} tickers")

    rows: list[dict[str, Any]] = []
    t_total = time.perf_counter()

    # universe 차원이 바뀌면 rankings + precompute 모두 재호출 필요 (top_n_stocks가 stock_days를 결정).
    for top_n in TOP_N_LIST:
        for rw in RANKING_WINDOW_LIST:
            runner.params.top_n_stocks = top_n
            runner.params.ranking_window = rw
            t_pre = time.perf_counter()
            runner.compute_rankings()
            runner.precompute()
            print(f"  precompute(top_n={top_n}, rw={rw}) {time.perf_counter()-t_pre:.1f}s")

            for variant_name, var in PARAM_VARIANTS.items():
                runner.params.vol_multiplier = var["vol_multiplier"]
                runner.params.sl_pct = var["sl_pct"]
                runner.params.trail_pct = var["trail_pct"]
                train = metrics_run(runner, TRAIN_START, TRAIN_END)
                test = metrics_run(runner, TEST_START, TEST_END)
                tr_pf = train["pf"] if train["pf"] is not None else 0.0
                te_pf = test["pf"] if test["pf"] is not None else 0.0
                rob = round(harmonic_mean(tr_pf, te_pf), 4)
                rows.append(
                    {
                        "params": {
                            "top_n_stocks": top_n,
                            "ranking_window": rw,
                            "variant": variant_name,
                            **var,
                        },
                        "train": train,
                        "test": test,
                        "robustness": rob,
                    }
                )
                print(
                    f"    [{variant_name:9s}] TRAIN PF={train['pf']} ret={train['ret_pct']:+5.1f}% "
                    f"MDD={train['mdd_pct']:+5.1f}% | TEST PF={test['pf']} ret={test['ret_pct']:+5.1f}% MDD={test['mdd_pct']:+5.1f}% | rob={rob}"
                )

    elapsed = time.perf_counter() - t_total
    print(f"[F2] grid finished in {elapsed:.1f}s")

    rows_sorted = sorted(rows, key=lambda r: r["robustness"], reverse=True)
    payload = {
        "cost_pct": COST,
        "train_period": {"start": TRAIN_START, "end": TRAIN_END},
        "test_period": {"start": TEST_START, "end": TEST_END},
        "elapsed_seconds": round(elapsed, 1),
        "n_combos": len(rows),
        "top10_by_robustness": rows_sorted[:10],
        "all": rows_sorted,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[F2] wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
