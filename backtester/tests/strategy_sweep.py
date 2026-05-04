#!/usr/bin/env python3
"""전략 sweep 러너.

ParquetDataProvider + LeanClient.backtest_strategy를 사용해
- 등록된 모든 프리셋 전략에 대해
- 동일한 유니버스/기간으로 백테스트를 돌리고
- 핵심 메트릭(Sharpe/MDD/CAGR/Trades/Excess vs KOSPI)을 CSV에 저장한다.

Phase 1 (일봉, 8년): 10개 전략 × 유동성 상위 N
Phase 2 (5분봉, 1년): 인트라데이 전략 (별도 정의 필요)

사용 예:
    uv run python tests/strategy_sweep.py phase1 --top 100 --start 2018-01-01 --end 2026-03-25
    uv run python tests/strategy_sweep.py phase1 --top 30 --start 2024-01-01 --end 2024-12-31  # 빠른 검증
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from kis_backtest import LeanClient
from kis_backtest.models import BacktestResult, Resolution
from kis_backtest.providers import ParquetDataProvider
from kis_backtest.strategies import StrategyRegistry
from kis_backtest.utils.universe import (
    save_universe,
    top_n_by_turnover,
)

logger = logging.getLogger("sweep")


PHASE1_STRATEGIES = [
    "sma_crossover",
    "momentum",
    "week52_high",
    "consecutive_moves",
    "ma_divergence",
    "false_breakout",
    "strong_close",
    "volatility_breakout",
    "short_term_reversal",
    "trend_filter_signal",
]


METRIC_FIELDS = [
    "strategy_id",
    "strategy_name",
    "universe_size",
    "start_date",
    "end_date",
    "total_return_pct",
    "cagr",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "total_trades",
    "win_rate",
    "profit_factor",
    "avg_win",
    "avg_loss",
    "kospi_return_pct",
    "excess_vs_kospi",
    "duration_seconds",
    "success",
    "error",
]


def kospi_return(provider: ParquetDataProvider, start: str, end: str) -> Optional[float]:
    """KOSPI(KODEX 200 ETF) 시작-종료 수익률."""
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    bars = provider.get_index_history("0001", s, e)
    if len(bars) < 2:
        return None
    return float(bars[-1].close / bars[0].close - 1.0)


def to_metrics_row(
    strategy_id: str,
    strategy_name: str,
    symbols: List[str],
    start: str,
    end: str,
    result: Optional[BacktestResult],
    kospi_ret: Optional[float],
    error: Optional[str] = None,
) -> Dict[str, object]:
    if result is None:
        return {
            "strategy_id": strategy_id,
            "strategy_name": strategy_name,
            "universe_size": len(symbols),
            "start_date": start,
            "end_date": end,
            "total_return_pct": "",
            "cagr": "",
            "sharpe_ratio": "",
            "sortino_ratio": "",
            "max_drawdown": "",
            "total_trades": "",
            "win_rate": "",
            "profit_factor": "",
            "avg_win": "",
            "avg_loss": "",
            "kospi_return_pct": kospi_ret if kospi_ret is not None else "",
            "excess_vs_kospi": "",
            "duration_seconds": "",
            "success": False,
            "error": error or "",
        }

    excess = (
        round(result.total_return_pct - kospi_ret, 6)
        if kospi_ret is not None
        else ""
    )
    return {
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "universe_size": len(symbols),
        "start_date": start,
        "end_date": end,
        "total_return_pct": round(result.total_return_pct, 6),
        "cagr": round(result.cagr, 6),
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "max_drawdown": round(result.max_drawdown, 6),
        "total_trades": result.total_trades,
        "win_rate": round(result.win_rate, 6),
        "profit_factor": result.profit_factor,
        "avg_win": result.average_win,
        "avg_loss": result.average_loss,
        "kospi_return_pct": round(kospi_ret, 6) if kospi_ret is not None else "",
        "excess_vs_kospi": excess,
        "duration_seconds": round(result.duration_seconds or 0, 2),
        "success": bool(result.success),
        "error": "",
    }


def run_phase(
    *,
    phase_label: str,
    strategy_ids: List[str],
    symbols: List[str],
    start: str,
    end: str,
    output_dir: Path,
    initial_cash: float = 100_000_000,
    market_type: str = "krx",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"{phase_label}_{timestamp}.csv"

    provider = ParquetDataProvider()
    client = LeanClient(data_provider=provider)

    save_universe(symbols, output_dir / f"{phase_label}_{timestamp}_universe.txt", phase_label)

    logger.info(
        "[%s] %d strategies × %d symbols, %s → %s",
        phase_label,
        len(strategy_ids),
        len(symbols),
        start,
        end,
    )
    kospi_ret = kospi_return(provider, start, end)
    logger.info("[%s] KOSPI(069500) 기간 수익률: %s", phase_label, kospi_ret)

    rows: List[Dict[str, object]] = []
    name_map = {s["id"]: s["name"] for s in StrategyRegistry.list_all()}

    for i, sid in enumerate(strategy_ids, 1):
        sname = name_map.get(sid, sid)
        logger.info("[%s] (%d/%d) %s — %s", phase_label, i, len(strategy_ids), sid, sname)
        t0 = time.time()
        try:
            result = client.backtest_strategy(
                strategy_id=sid,
                symbols=symbols,
                start_date=start,
                end_date=end,
                initial_cash=initial_cash,
                market_type=market_type,
            )
            elapsed = time.time() - t0
            row = to_metrics_row(sid, sname, symbols, start, end, result, kospi_ret)
            logger.info(
                "[%s]   ret=%.2f%% sharpe=%s mdd=%.2f%% trades=%d (%.1fs)",
                phase_label,
                result.total_return_pct * 100,
                result.sharpe_ratio,
                result.max_drawdown * 100,
                result.total_trades,
                elapsed,
            )
        except Exception as e:
            logger.exception("[%s]   FAILED: %s", phase_label, e)
            row = to_metrics_row(
                sid, sname, symbols, start, end, None, kospi_ret,
                error=f"{type(e).__name__}: {e}",
            )
        rows.append(row)

        # 부분 결과를 매번 저장 (중단되어도 데이터 남음)
        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=METRIC_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    logger.info("[%s] DONE — saved: %s", phase_label, csv_path)
    return csv_path


def cmd_phase1(args: argparse.Namespace) -> None:
    provider = ParquetDataProvider()
    lookback_start = datetime.strptime(args.start, "%Y-%m-%d").date()
    lookback_end = datetime.strptime(args.end, "%Y-%m-%d").date()

    symbols = top_n_by_turnover(
        provider,
        n=args.top,
        resolution=Resolution.DAILY,
        lookback_start=lookback_start,
        lookback_end=lookback_end,
        exclude_etfs=True,
    )
    if not symbols:
        raise SystemExit("유니버스가 비어 있습니다.")

    output_dir = REPO_ROOT / "examples" / "output" / "sweep"
    run_phase(
        phase_label=f"phase1_daily_top{args.top}",
        strategy_ids=PHASE1_STRATEGIES,
        symbols=symbols,
        start=args.start,
        end=args.end,
        output_dir=output_dir,
        initial_cash=args.cash,
    )


def cmd_smoke(args: argparse.Namespace) -> None:
    """1년 + 30종목으로 빠르게 전 전략 점검."""
    provider = ParquetDataProvider()
    symbols = top_n_by_turnover(
        provider,
        n=args.top,
        resolution=Resolution.DAILY,
        lookback_start=date(2024, 1, 1),
        lookback_end=date(2024, 12, 31),
        exclude_etfs=True,
    )
    output_dir = REPO_ROOT / "examples" / "output" / "sweep"
    run_phase(
        phase_label=f"smoke_top{args.top}",
        strategy_ids=PHASE1_STRATEGIES,
        symbols=symbols,
        start="2024-01-01",
        end="2024-12-31",
        output_dir=output_dir,
        initial_cash=args.cash,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Strategy sweep runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("phase1", help="일봉 8년 sweep")
    p1.add_argument("--top", type=int, default=200, help="유니버스 크기 (top-N by turnover)")
    p1.add_argument("--start", default="2018-01-01")
    p1.add_argument("--end", default="2026-03-25")
    p1.add_argument("--cash", type=float, default=100_000_000)
    p1.set_defaults(func=cmd_phase1)

    sm = sub.add_parser("smoke", help="빠른 점검 (1년, 작은 유니버스)")
    sm.add_argument("--top", type=int, default=30)
    sm.add_argument("--cash", type=float, default=100_000_000)
    sm.set_defaults(func=cmd_smoke)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args.func(args)


if __name__ == "__main__":
    main()
