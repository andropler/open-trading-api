"""P3 — V4.1 파라미터 그리드 재탐색 (TRAIN/TEST 이중 통과 게이트).

VALIDATION.md(v1)의 우려: alpha-hunter fit된 디폴트 파라미터가 진짜 OOS에선 평범.
이 스크립트는 4차원 그리드(lookback × vol_mult × sl × trail)에서 다음을 찾는다:
    - TRAIN(2023-03~2024-12, 진짜 OOS)에서도 충분히 robust
    - TEST(2025-01~2026-03, in-sample fit window)에서도 통과
    - 두 구간의 PF 모두 일정 임계 통과 (in-sample fit 회피)

robustness 메트릭: harmonic_mean(train_pf, test_pf)
    — TRAIN 또는 TEST 한쪽이 약하면 자동으로 깎임.
    — 둘 다 강해야만 점수 높음.

산출:
    backtester/scripts/validation_v2/p3_v41_param_grid.json
"""

from __future__ import annotations

import itertools
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
INITIAL_EQUITY = 10_000_000
MAX_POSITIONS = 3
COST = 0.55  # default — robustness 메트릭은 cost 고정에서 측정

GRID = {
    "breakout_lookback": [3, 4, 5, 6],
    "vol_multiplier": [1.5, 2.0, 2.5, 3.0],
    "sl_pct": [3.0, 5.0, 7.0],
    "trail_pct": [0.3, 0.5, 0.8, 1.0],
}

OUT_PATH = Path(__file__).with_suffix(".json")


def harmonic_mean(a: float, b: float) -> float:
    """Harmonic mean — penalizes any weak side severely. Returns 0 if either ≤ 0."""
    if a <= 0 or b <= 0:
        return 0.0
    return 2.0 * a * b / (a + b)


def metrics_from_run(runner: KRIntradayBreakoutV41Backtester, start: str, end: str) -> dict[str, Any]:
    runner.run(
        initial_equity=INITIAL_EQUITY,
        max_positions=MAX_POSITIONS,
        start_date=start,
        end_date=end,
    )
    result = runner.to_backtest_result(start_date=start, end_date=end, initial_equity=INITIAL_EQUITY)
    pf = result.profit_factor
    return {
        "trades": int(result.total_trades),
        "win_rate_pct": round(result.win_rate * 100, 2),
        "return_pct": round(result.total_return_pct * 100, 2),
        "mdd_pct": round(result.max_drawdown * 100, 2),
        "pf": (round(pf, 4) if pf != float("inf") else None),
        "sharpe": round(result.sharpe_ratio, 3),
    }


def main() -> None:
    print(f"[P3] loading 1H data from {DATA_DIR}")
    runner = KRIntradayBreakoutV41Backtester(data_dir=DATA_DIR, params=BreakoutV41Params(cost_pct=COST))
    runner.load_data()
    print(f"[P3] loaded {len(runner.raw_data)} tickers")

    rows: list[dict[str, Any]] = []
    combos = list(
        itertools.product(
            GRID["breakout_lookback"],
            GRID["vol_multiplier"],
            GRID["sl_pct"],
            GRID["trail_pct"],
        )
    )
    print(f"[P3] running {len(combos)} parameter combinations...")
    t_start = time.perf_counter()

    last_lookback: int | None = None
    last_vol_mult: float | None = None  # vol_avg_window는 고정이라 vol_mult는 precompute 영향 없음

    for i, (lookback, vol_mult, sl, trail) in enumerate(combos, 1):
        # precompute는 lookback에 의존 (prev_n_high). vol_mult/sl/trail은 run에서만.
        # → lookback이 바뀔 때만 rankings/precompute 재호출.
        if lookback != last_lookback:
            runner.params.breakout_lookback = lookback
            runner.params.vol_multiplier = vol_mult  # placeholder — precompute는 영향 안 받음
            runner.params.sl_pct = sl
            runner.params.trail_pct = trail
            t_pre = time.perf_counter()
            runner.compute_rankings()
            runner.precompute()
            print(f"  [{i}/{len(combos)}] precompute(lookback={lookback}) {time.perf_counter()-t_pre:.1f}s")
            last_lookback = lookback

        # update run-time params
        runner.params.vol_multiplier = vol_mult
        runner.params.sl_pct = sl
        runner.params.trail_pct = trail
        # trail_activation은 디폴트 0.5% 유지

        train = metrics_from_run(runner, TRAIN_START, TRAIN_END)
        test = metrics_from_run(runner, TEST_START, TEST_END)
        train_pf = train["pf"] if train["pf"] is not None else 0.0
        test_pf = test["pf"] if test["pf"] is not None else 0.0
        rob = round(harmonic_mean(train_pf, test_pf), 4)

        rows.append(
            {
                "params": {
                    "breakout_lookback": lookback,
                    "vol_multiplier": vol_mult,
                    "sl_pct": sl,
                    "trail_pct": trail,
                },
                "train": train,
                "test": test,
                "robustness": rob,
            }
        )

    elapsed = time.perf_counter() - t_start
    print(f"[P3] grid run finished in {elapsed:.1f}s")

    # default config row (디폴트는 그리드 안에 포함됨: lookback=4, vol_mult=2.0, sl=5.0, trail=0.5)
    default_match = next(
        (
            r
            for r in rows
            if r["params"]["breakout_lookback"] == 4
            and r["params"]["vol_multiplier"] == 2.0
            and r["params"]["sl_pct"] == 5.0
            and r["params"]["trail_pct"] == 0.5
        ),
        None,
    )

    # ranking
    rows_sorted = sorted(rows, key=lambda r: r["robustness"], reverse=True)
    top10 = rows_sorted[:10]

    # gate: TRAIN PF >= 1.05 AND TEST PF >= 1.20
    gated = [
        r
        for r in rows_sorted
        if (r["train"]["pf"] or 0) >= 1.05 and (r["test"]["pf"] or 0) >= 1.20
    ]

    payload = {
        "grid": GRID,
        "cost_pct": COST,
        "train_period": {"start": TRAIN_START, "end": TRAIN_END},
        "test_period": {"start": TEST_START, "end": TEST_END},
        "elapsed_seconds": round(elapsed, 1),
        "n_combos": len(rows),
        "default_config": default_match,
        "top10_by_robustness": top10,
        "n_gated": len(gated),
        "gated_top10": gated[:10],
        "all": rows_sorted,  # full grid for further inspection
    }

    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[P3] wrote {OUT_PATH}")
    print(f"[P3] default config robustness rank: {[r['params'] for r in rows_sorted].index(default_match['params']) + 1 if default_match else 'N/A'}")
    print(f"[P3] top 5 by robustness:")
    for r in top10[:5]:
        p = r["params"]
        print(
            f"  L{p['breakout_lookback']} V{p['vol_multiplier']} SL{p['sl_pct']} T{p['trail_pct']} | "
            f"TRAIN PF={r['train']['pf']} TEST PF={r['test']['pf']} robustness={r['robustness']}"
        )


if __name__ == "__main__":
    main()
