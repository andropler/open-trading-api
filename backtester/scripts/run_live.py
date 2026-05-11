"""5m Composite 라이브 트레이더 매일 실행 스크립트.

현재 구현 범위:
- LiveTrader.build_live_trader(.env.live) 로 모든 컴포넌트 wire-up
- morning_routine 실행 → 텔레그램 STARTUP 알림 + entries_allowed 출력
- engines=[] 이므로 dry_run_trade_step 호출 시 빈 결과 (신호 엔진 미구현)

추가 예정:
- WS subscribe (price + fill) 구독
- 장중 main loop (09:00~15:30)
- shutdown 처리

실행: cd backtester && .venv/bin/python -m scripts.run_live
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--env", type=Path, default=REPO_ROOT / ".env.live")
    p.add_argument(
        "--asof",
        type=lambda s: date.fromisoformat(s),
        default=date.today(),
        help="ISO date (YYYY-MM-DD). 기본 today.",
    )
    p.add_argument("--market-symbol", default="069500")
    p.add_argument(
        "--no-telegram",
        action="store_true",
        help="텔레그램 알림 비활성화 (디버그용)",
    )
    p.add_argument(
        "--state-dir",
        type=Path,
        default=Path.home() / "KIS" / "live_state",
        help="상태 파일 디렉토리 (positions/HALT/cache)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    sys.path.insert(0, str(REPO_ROOT))

    from kis_backtest.live.orchestrator.builder import build_live_trader

    print(f"[run_live] env={args.env} asof={args.asof} symbol={args.market_symbol}")
    trader = build_live_trader(
        args.env,
        today=args.asof,
        engines=[],  # 신호 엔진 미구현 — 다음 이터레이션
        state_dir=args.state_dir,
        market_symbol=args.market_symbol,
        enable_telegram=not args.no_telegram,
    )
    print(f"[run_live] LiveTrader built mode={trader.config.mode}")

    routine = trader.run_morning(args.asof)
    print(
        f"[run_live] morning entries_allowed={routine.entries_allowed} "
        f"bull_20_60={routine.flags.m_bull_20_60} "
        f"no_shock={routine.flags.m_no_1d_shock} "
        f"no_dd5={routine.flags.m_no_5d_drawdown} "
        f"rows={routine.daily_rows}"
    )

    orders = trader.run_trade(routine, dry_run=True)
    print(f"[run_live] dry_run orders={len(orders)} (engines=[], 항상 0)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
