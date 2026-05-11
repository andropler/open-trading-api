"""5m Composite 라이브 트레이더 매일 실행 스크립트.

기본 (단순 morning 점검):
  cd backtester && .venv/bin/python -m scripts.run_live

장중 main loop (KIS WS price+fill 구독 + 5m 신호 평가 + 주문):
  .venv/bin/python -m scripts.run_live --loop \\
      --universe 005930,000660,373220 --asof 2026-05-12

장 종료 시각 등 옵션은 --help 참조.
"""

from __future__ import annotations

import argparse
import logging
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
    p.add_argument(
        "--loop",
        action="store_true",
        help="장중 main loop 활성화 (KIS WS subscribe + 5m 신호 평가)",
    )
    p.add_argument(
        "--universe",
        type=str,
        default="",
        help="--loop 모드에서 구독할 종목 코드 (쉼표 구분, 예: 005930,000660)",
    )
    p.add_argument(
        "--history-days",
        type=int,
        default=60,
        help="universe 종목별 일봉 fetch 기간 (달력일 단위 곱 2 사용)",
    )
    p.add_argument(
        "--hts-id",
        default=None,
        help="KIS HTS ID (체결통보용). 미지정 시 ~/KIS/config/kis_devlp.yaml 의 my_htsid 사용",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="execute_step dry_run (기본 True)",
    )
    p.add_argument(
        "--live-orders",
        action="store_true",
        help="실 주문 발행 (dry_run=False). 페이퍼 검증 후에만 켜기",
    )
    return p.parse_args()


def _morning_only(args) -> int:
    from kis_backtest.live.orchestrator.builder import build_live_trader

    print(
        f"[run_live] env={args.env} asof={args.asof} symbol={args.market_symbol}"
    )
    trader = build_live_trader(
        args.env,
        today=args.asof,
        engines=[],
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


def _loop_mode(args) -> int:
    from kis_backtest.live.orchestrator.builder import build_full_session
    from kis_backtest.live.orchestrator.trading_day import run_trading_day
    from kis_backtest.live.signal.reclaim_engine import (
        IntradayReclaimParams,
        LiveReclaimEngine,
    )

    universe = [s.strip() for s in args.universe.split(",") if s.strip()]
    if not universe:
        print("[run_live] --loop 모드에는 --universe 가 필수")
        return 1

    print(
        f"[run_live] loop mode env={args.env} asof={args.asof} "
        f"universe={universe} hts_id={'set' if args.hts_id else 'yaml'}"
    )

    reclaim = LiveReclaimEngine(params=IntradayReclaimParams())
    session = build_full_session(
        args.env,
        today=args.asof,
        engines=[reclaim],
        state_dir=args.state_dir,
        market_symbol=args.market_symbol,
        enable_telegram=not args.no_telegram,
        hts_id=args.hts_id,
    )

    session.ws_launcher.subscribe_price(universe, session.trader.on_price)
    session.fill_subscriber.start()
    session.ws_launcher.start()
    print("[run_live] WS thread started")

    try:
        result = run_trading_day(
            session.trader,
            reclaim,
            session.trader.fetcher,
            asof_date=args.asof,
            universe=universe,
            history_days=args.history_days,
            dry_run=not args.live_orders,
        )
        print(
            f"[run_live] trading day complete entries_allowed={result.entries_allowed} "
            f"eval_cycles={result.eval_cycles} signals={result.signals_seen} "
            f"orders={result.orders_submitted} halt={result.halt_triggered}"
        )
    finally:
        session.ws_launcher.stop()
        print("[run_live] WS thread stopped")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    sys.path.insert(0, str(REPO_ROOT))
    if args.loop:
        return _loop_mode(args)
    return _morning_only(args)


if __name__ == "__main__":
    sys.exit(main())
