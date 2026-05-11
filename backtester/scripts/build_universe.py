"""매일 아침 장 시작 전 실행 — 거래대금 상위 N 종목으로 universe 결정 + 저장.

cron 예: 08:00 KST 평일
  cd backtester && .venv/bin/python -m scripts.build_universe --top 30 --out ~/KIS/live_state/universe.json

저장 포맷:
{
  "asof_date": "2026-05-12",
  "rank_by": "trading_value",
  "market": "ALL",
  "top_n": 30,
  "symbols": ["005930", "000660", ...],
  "entries": [
    {"ticker": "005930", "name": "삼성전자", "price": 70000, "volume": ..., "trading_value": ..., "rank": 1},
    ...
  ]
}
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--env", type=Path, default=REPO_ROOT / ".env.live")
    p.add_argument(
        "--out",
        type=Path,
        default=Path.home() / "KIS" / "live_state" / "universe.json",
        help="universe.json 저장 경로",
    )
    p.add_argument("--top", type=int, default=50, help="상위 N (보통주 필터 후 기준)")
    p.add_argument(
        "--stock-list",
        type=Path,
        default=Path("/Users/benjamin/personal_workspace/shared_data/kr_stocks/_stock_list.parquet"),
        help="보통주 마스터 parquet (ETF 자동 제외용). 비활성: --no-stock-list",
    )
    p.add_argument(
        "--no-stock-list",
        action="store_true",
        help="stock_list 필터 비활성 — KIS 응답 그대로 (ETF 포함)",
    )
    p.add_argument(
        "--rank-by",
        choices=["volume", "trading_value", "volume_growth", "turnover"],
        default="trading_value",
    )
    p.add_argument("--market", choices=["ALL", "KOSPI", "KOSDAQ"], default="ALL")
    p.add_argument("--min-price", type=int, default=5_000)
    p.add_argument(
        "--include-etf",
        action="store_true",
        help="기본은 ETF 제외(FID_DIV_CLS_CODE=1=보통주). 켜면 전체",
    )
    p.add_argument(
        "--asof",
        type=lambda s: date.fromisoformat(s),
        default=date.today(),
        help="저장 메타에 기록할 asof_date (기본 today)",
    )
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = parse_args()
    sys.path.insert(0, str(REPO_ROOT))

    from kis_backtest.live.config.credentials import LiveConfig
    from kis_backtest.live.config.kis_yaml_sync import sync_kis_yaml

    if not args.env.exists():
        print(f"[FAIL] .env.live 없음: {args.env}")
        return 1
    config = LiveConfig.from_env(args.env)
    sync_kis_yaml(config.kis)

    from kis_backtest.live.data.volume_rank import fetch_volume_rank
    from kis_backtest.providers.kis.auth import KISAuth

    auth = KISAuth(
        app_key=config.kis.appkey,
        app_secret=config.kis.appsecret,
        account_no=config.kis.account_no,
        is_paper=(config.mode == "vps"),
    )

    allowed: set[str] | None = None
    if not args.no_stock_list:
        from kis_backtest.live.data.stock_list import load_stock_universe

        allowed = load_stock_universe(args.stock_list)
        print(
            f"[build_universe] stock_list loaded: {len(allowed)} 종목 "
            f"(ETF 등 자동 제외 활성)"
        )

    entries = fetch_volume_rank(
        auth,
        market=args.market,
        rank_by=args.rank_by,
        top_n=args.top,
        min_price=args.min_price,
        exclude_etf=not args.include_etf,
        allowed_tickers=allowed,
    )
    print(f"[build_universe] fetched {len(entries)} entries (top {args.top})")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "asof_date": args.asof.isoformat(),
        "rank_by": args.rank_by,
        "market": args.market,
        "top_n": args.top,
        "min_price": args.min_price,
        "exclude_etf": not args.include_etf,
        "symbols": [e.ticker for e in entries],
        "entries": [
            {
                "ticker": e.ticker,
                "name": e.name,
                "price": e.price,
                "volume": e.volume,
                "trading_value": e.trading_value,
                "rank": e.rank,
            }
            for e in entries
        ],
    }
    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(args.out)
    print(f"[build_universe] saved → {args.out}")
    for e in entries[:10]:
        print(f"  {e.rank:>2}. {e.ticker} {e.name} value={e.trading_value:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
