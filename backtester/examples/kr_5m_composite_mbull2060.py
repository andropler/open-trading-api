"""Run the final KR 5m composite m_bull_20_60 backtest.

This is an open-trading-api backtester entry point that executes the custom
parquet-based backtester in `kis_backtest.custom`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kis_backtest.custom.kr_5m_composite_mbull2060 import (
    CompositeMBull2060Params,
    KR5mCompositeMBull2060Backtester,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KR 5m composite m_bull_20_60 backtest")
    parser.add_argument(
        "--alpha-root",
        type=Path,
        default=None,
        help="Path to sibling alpha-hunter workspace containing the validated signal builders.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for JSON/Markdown/trade CSV artifacts.",
    )
    parser.add_argument("--initial-equity", type=float, default=10_000_000)
    parser.add_argument("--cost-pct", type=float, default=0.55)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    params = CompositeMBull2060Params(
        initial_equity=args.initial_equity,
        cost_pct=args.cost_pct,
    )
    if args.alpha_root is not None:
        params.alpha_root = args.alpha_root
    if args.output_dir is not None:
        params.output_dir = args.output_dir

    backtester = KR5mCompositeMBull2060Backtester(params=params)
    result = backtester.run()
    summary = result.summary
    print(
        f"{backtester.STRATEGY_ID} signals={summary['signal_count']} trades={summary['trades']} "
        f"total={summary['total_return']:.1f}% pf055={summary['pf_055']:.3f} "
        f"pf100={summary['pf_100']:.3f} mdd={summary['mdd']:.1f}% "
        f"elapsed={backtester.last_run_seconds:.1f}s",
        flush=True,
    )
    if result.artifacts:
        print(json.dumps(result.artifacts, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
