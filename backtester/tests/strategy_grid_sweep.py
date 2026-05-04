#!/usr/bin/env python3
"""전략별 핵심 파라미터 그리드 최적화 sweep.

각 프리셋 전략의 핵심 파라미터 1~2개에 대해 작은 그리드를 돌려
- 디폴트 대비 어디까지 수익률을 끌어올릴 수 있는지
- "디폴트 거래 0건" 같은 전략을 살릴 수 있는지
를 평가한다.

`tests/strategy_sweep.py`와 동일한 ParquetDataProvider + Lean 경로를 사용.
유니버스/기간은 sweep보다 작게 잡아 그리드 비용을 통제한다.

사용:
    uv run python tests/strategy_grid_sweep.py --top 50 --start 2018-01-01 --end 2026-03-25
"""

from __future__ import annotations

import argparse
import csv
import itertools
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from kis_backtest import LeanClient
from kis_backtest.models import Resolution
from kis_backtest.providers import ParquetDataProvider
from kis_backtest.utils.universe import top_n_by_turnover

logger = logging.getLogger("grid")


# 각 전략별 (param_name, [values]) 두 개 — 핵심 차원 두 축
GRID_DEFS: Dict[str, List[Tuple[str, List[Any]]]] = {
    "sma_crossover": [
        ("fast_period", [3, 5, 10, 20]),
        ("slow_period", [20, 50, 100, 200]),
    ],
    "momentum": [
        ("lookback", [20, 60, 120, 252]),
        ("threshold", [-5.0, 0.0, 2.0, 5.0]),
    ],
    "week52_high": [
        ("lookback", [60, 126, 252, 504]),
        ("stop_loss_pct", [3.0, 5.0, 10.0]),
    ],
    "consecutive_moves": [
        ("up_days", [2, 3, 5, 7]),
        ("down_days", [2, 3, 5, 7]),
    ],
    "ma_divergence": [
        ("period", [10, 20, 40]),
        ("buy_ratio", [0.80, 0.85, 0.90, 0.95]),
    ],
    "false_breakout": [
        ("lookback", [10, 20, 40, 60]),
        ("exit_days", [2, 3, 5, 8]),
    ],
    "strong_close": [
        ("min_close_ratio", [0.6, 0.7, 0.8, 0.9]),
        ("stop_loss_pct", [3.0, 5.0, 10.0]),
    ],
    "volatility_breakout": [
        ("atr_period", [5, 10, 14, 20]),
        ("lookback", [10, 20, 40]),
    ],
    "short_term_reversal": [
        ("period", [3, 5, 10, 15]),
        ("threshold_pct", [2.0, 3.0, 5.0, 8.0]),
    ],
    "trend_filter_signal": [
        ("trend_period", [20, 60, 120, 200]),
        ("stop_loss_pct", [3.0, 5.0, 10.0]),
    ],
}


METRIC_FIELDS = [
    "strategy_id",
    "param_name1", "param_value1",
    "param_name2", "param_value2",
    "total_return_pct", "cagr",
    "sharpe_ratio", "sortino_ratio", "max_drawdown",
    "total_trades", "win_rate", "profit_factor",
    "duration_seconds", "success", "error",
]


def run_grid_for_strategy(
    client: LeanClient,
    strategy_id: str,
    grid: List[Tuple[str, List[Any]]],
    symbols: List[str],
    start: str,
    end: str,
    initial_cash: float,
    out_path: Path,
    rows_acc: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    name1, values1 = grid[0]
    name2, values2 = grid[1]

    logger.info(
        "[%s] grid %s × %s = %d combos",
        strategy_id,
        f"{name1}{values1}",
        f"{name2}{values2}",
        len(values1) * len(values2),
    )

    for v1, v2 in itertools.product(values1, values2):
        # sma_crossover의 fast >= slow는 무의미 → skip
        if strategy_id == "sma_crossover" and v1 >= v2:
            continue

        params = {name1: v1, name2: v2}
        t0 = time.time()
        try:
            r = client.backtest_strategy(
                strategy_id=strategy_id,
                symbols=symbols,
                start_date=start,
                end_date=end,
                params=params,
                initial_cash=initial_cash,
            )
            elapsed = time.time() - t0
            row = {
                "strategy_id": strategy_id,
                "param_name1": name1, "param_value1": v1,
                "param_name2": name2, "param_value2": v2,
                "total_return_pct": round(r.total_return_pct, 6),
                "cagr": round(r.cagr, 6),
                "sharpe_ratio": r.sharpe_ratio,
                "sortino_ratio": r.sortino_ratio,
                "max_drawdown": round(r.max_drawdown, 6),
                "total_trades": r.total_trades,
                "win_rate": round(r.win_rate, 6),
                "profit_factor": r.profit_factor,
                "duration_seconds": round(elapsed, 1),
                "success": True,
                "error": "",
            }
            logger.info(
                "  [%s %s=%s %s=%s] ret=%+.1f%% sharpe=%s mdd=%.1f%% trades=%d pf=%.2f (%.1fs)",
                strategy_id, name1, v1, name2, v2,
                r.total_return_pct * 100, r.sharpe_ratio, r.max_drawdown * 100,
                r.total_trades, r.profit_factor, elapsed,
            )
        except Exception as e:
            row = {
                "strategy_id": strategy_id,
                "param_name1": name1, "param_value1": v1,
                "param_name2": name2, "param_value2": v2,
                "total_return_pct": "", "cagr": "",
                "sharpe_ratio": "", "sortino_ratio": "", "max_drawdown": "",
                "total_trades": "", "win_rate": "", "profit_factor": "",
                "duration_seconds": "", "success": False,
                "error": f"{type(e).__name__}: {e}",
            }
            logger.warning("  [%s %s=%s %s=%s] FAILED: %s",
                           strategy_id, name1, v1, name2, v2, e)

        rows_acc.append(row)
        # 매 행마다 저장 (중단되어도 잃지 않음)
        with out_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=METRIC_FIELDS)
            w.writeheader()
            w.writerows(rows_acc)

    return rows_acc


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy grid sweep")
    parser.add_argument("--top", type=int, default=50, help="유니버스 크기")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2026-03-25")
    parser.add_argument("--cash", type=float, default=100_000_000)
    parser.add_argument(
        "--strategies", nargs="*", default=None,
        help="이 리스트의 전략만 그리드 평가 (지정 안하면 전체)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    provider = ParquetDataProvider()
    client = LeanClient(data_provider=provider)

    symbols = top_n_by_turnover(
        provider, n=args.top, resolution=Resolution.DAILY,
        lookback_start=datetime.strptime(args.start, "%Y-%m-%d").date(),
        lookback_end=datetime.strptime(args.end, "%Y-%m-%d").date(),
        exclude_etfs=True,
    )
    logger.info("Universe: top %d symbols (%s ... %s)", len(symbols), symbols[0], symbols[-1])

    output_dir = REPO_ROOT / "examples" / "output" / "sweep"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"grid_top{args.top}_{timestamp}.csv"

    target_ids = args.strategies if args.strategies else list(GRID_DEFS.keys())

    rows: List[Dict[str, Any]] = []
    for sid in target_ids:
        if sid not in GRID_DEFS:
            logger.warning("스킵 (정의 없음): %s", sid)
            continue
        run_grid_for_strategy(
            client, sid, GRID_DEFS[sid], symbols,
            args.start, args.end, args.cash, out_path, rows,
        )

    logger.info("DONE — saved: %s", out_path)


if __name__ == "__main__":
    main()
