#!/usr/bin/env python3
"""Run the KR 1H Breakout V4.1 port inside open-trading-api."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kis_backtest import (
    BreakoutV41Params,
    KRIntradayBreakoutV41Backtester,
    LeanClient,
    detect_default_parquet_data_dir,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run KR 1H Breakout V4.1 custom backtest")
    parser.add_argument("--start-date", default="2025-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default="2026-03-31", help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--initial-capital", type=int, default=10_000_000, help="Initial capital in KRW")
    parser.add_argument("--max-positions", type=int, default=3, help="Maximum concurrent positions")
    parser.add_argument("--data-dir", default=str(detect_default_parquet_data_dir()), help="Path to KR 1H parquet dataset")
    parser.add_argument("--output-dir", default="examples/output/kr_intraday_breakout_v41", help="Directory for HTML/JSON output")
    parser.add_argument("--export-lean-artifacts", action="store_true", help="Export hourly CSV and ranking JSON into .lean-workspace")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runner = KRIntradayBreakoutV41Backtester(
        data_dir=args.data_dir,
        params=BreakoutV41Params(),
    )
    runner.load_data()
    runner.compute_rankings()
    runner.precompute()
    runner.run(
        initial_equity=args.initial_capital,
        max_positions=args.max_positions,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    artifacts = None
    if args.export_lean_artifacts:
        artifacts = runner.export_supporting_artifacts(".lean-workspace")

    result = runner.to_backtest_result(
        start_date=args.start_date,
        end_date=args.end_date,
        initial_equity=args.initial_capital,
    )

    report_path = LeanClient().report(
        result=result,
        output_path=output_dir / "kr_intraday_breakout_v41_report.html",
        title="KR 1H Breakout V4.1 Backtest",
        subtitle=f"{args.start_date} ~ {args.end_date}",
    )

    summary = {
        "strategy_id": result.strategy_id,
        "data_dir": str(Path(args.data_dir).resolve()),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "initial_capital": args.initial_capital,
        "max_positions": args.max_positions,
        "trade_count": result.total_trades,
        "total_return_pct": round(result.total_return_pct * 100, 2),
        "cagr_pct": round(result.cagr * 100, 2),
        "max_drawdown_pct": round(result.max_drawdown * 100, 2),
        "sharpe_ratio": round(result.sharpe_ratio, 3),
        "profit_factor": result.profit_factor,
        "win_rate_pct": round(result.win_rate * 100, 2),
        "report_path": str(report_path),
        "artifacts": artifacts,
    }
    summary_path = output_dir / "kr_intraday_breakout_v41_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 68)
    print("KR 1H Breakout V4.1")
    print("=" * 68)
    print(f"Data dir      : {Path(args.data_dir).resolve()}")
    print(f"Period        : {args.start_date} -> {args.end_date}")
    print(f"Trades        : {result.total_trades}")
    print(f"Total return  : {result.total_return_pct * 100:+.2f}%")
    print(f"CAGR          : {result.cagr * 100:+.2f}%")
    print(f"Max drawdown  : {result.max_drawdown * 100:.2f}%")
    print(f"Sharpe        : {result.sharpe_ratio:.3f}")
    print(f"Profit factor : {result.profit_factor:.3f}")
    print(f"Win rate      : {result.win_rate * 100:.2f}%")
    print(f"Report        : {report_path}")
    print(f"Summary       : {summary_path}")
    if artifacts:
        print(f"Lean artifacts: {artifacts['hourly_dir']}")


if __name__ == "__main__":
    main()
