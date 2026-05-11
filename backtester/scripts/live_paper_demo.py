"""5m Composite 라이브 봇 e2e dry-run 데모.

실행: cd backtester && .venv/bin/python -m scripts.live_paper_demo

mock fetcher/engine/null telegram 으로 LiveTrader 전체 흐름을 시뮬한다. 실
KIS API 호출 0, 자격증명 무관. 사용자가 트레이더 통합 흐름이 정상 동작하는지
즉시 확인하는 용도.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from kis_backtest.live.config.credentials import (
    KISCreds,
    LiveConfig,
    TelegramCreds,
    TradingLimits,
)
from kis_backtest.live.data.bar_aggregator import FiveMinuteBarAggregator
from kis_backtest.live.data.bar_buffer import FiveMinuteBarBuffer
from kis_backtest.live.data.cache import DailyOHLCVCache
from kis_backtest.live.orchestrator.live_trader import LiveTrader, build_engines
from kis_backtest.live.orchestrator.monitors import Api5xxMonitor, WsHealthMonitor
from kis_backtest.live.position.tracker import PositionTracker
from kis_backtest.live.risk.killswitch import Killswitch
from kis_backtest.live.signal.models import ExitProfile, Signal


def _market_uptrend(start: date, n: int) -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="B")
    closes = [100.0 + i * 0.5 for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1_000] * n,
        }
    )


class MockFetcher:
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def fetch_daily(self, symbol, start_date, end_date):
        return self.df.copy()


class MockExecutor:
    def __init__(self):
        self.calls = []

    def submit_order(self, symbol, side, quantity, order_type="market", price=0):
        self.calls.append((symbol, side, quantity))
        return f"DEMO-{len(self.calls)}"


def _signal(ticker: str, hhmm: int, entry: float = 70_000.0) -> Signal:
    h, m = divmod(hhmm, 100)
    return Signal(
        source="reclaim",
        variant="reclaim_strict",
        ticker=ticker,
        entry_ts=pd.Timestamp(f"2026-05-07 {h:02d}:{m:02d}:00"),
        entry_price=entry,
        stop_price=entry * 0.97,
        profile=ExitProfile(
            stop_loss_pct=3.0,
            take_profit_pct=10.0,
            trail_activation_pct=5.0,
            trail_pct=4.0,
            max_hold_days=1,
        ),
        priority=5.0,
    )


@dataclass
class MockEngine:
    name: str
    signals: list = field(default_factory=list)

    def candidate_signals(self, asof_date):
        return list(self.signals)


@dataclass
class FakePrice:
    symbol: str
    time: str
    price: int
    volume: int


@dataclass
class FakeFillNotice:
    customer_id: str = "DEMO"
    account_no: str = "00000-00"
    order_no: str = "DEMO-1"
    order_qty: int = 71
    side: str = "02"
    symbol: str = "005930"
    fill_qty: int = 71
    fill_price: int = 70_000
    fill_time: str = "2026-05-07T09:35:00"
    is_fill: bool = True
    is_rejected: bool = False


def main() -> None:
    asof = date(2026, 5, 7)
    print(f"[DEMO] asof={asof} mode=vps strategy=composite\n")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = DailyOHLCVCache(root / "daily")
        buffer = FiveMinuteBarBuffer(snapshot_dir=root / "snap")
        aggregator = FiveMinuteBarAggregator(buffer=buffer, today=asof)
        tracker = PositionTracker(root / "positions.json")
        killswitch = Killswitch(
            halt_flag_path=root / "HALT.flag",
            archive_dir=root / "halts",
            capital_krw=5_000_000,
        )
        config = LiveConfig(
            mode="vps",
            telegram=TelegramCreds(bot_token="demo", chat_id="0"),
            kis=KISCreds(
                appkey="demo", appsecret="demo", account_no="0", mode="vps"
            ),
            limits=TradingLimits(
                capital_krw=5_000_000,
                daily_loss_pct=3.0,
                cumulative_loss_pct=8.0,
            ),
        )
        trader = LiveTrader(
            config=config,
            fetcher=MockFetcher(_market_uptrend(date(2026, 1, 1), 100)),
            cache=cache,
            bar_buffer=buffer,
            aggregator=aggregator,
            executor=MockExecutor(),
            tracker=tracker,
            killswitch=killswitch,
            ws_monitor=WsHealthMonitor(),
            api_monitor=Api5xxMonitor(),
            engines=build_engines(
                MockEngine(name="reclaim_demo", signals=[_signal("005930", 935)])
            ),
            telegram=None,
        )

        print("[1] morning_routine — 시장 레짐 평가")
        routine = trader.run_morning(asof)
        print(
            f"    entries_allowed={routine.entries_allowed} "
            f"bull_20_60={routine.flags.m_bull_20_60} "
            f"rows={routine.daily_rows}\n"
        )

        print("[2] dry_run_trade_step + execute_step (dry_run=True)")
        orders = trader.run_trade(routine, dry_run=True)
        for o in orders:
            print(
                f"    order ticker={o.request.ticker} qty={o.request.qty} "
                f"reason={o.reason}"
            )
        print()

        print("[3] on_price — 5m bar 집계 시뮬 (3 ticks)")
        for hhmmss, price, vol in [
            ("093001", 70_000, 100),
            ("093215", 70_500, 50),
            ("093430", 69_800, 200),
        ]:
            trader.on_price("005930", FakePrice("005930", hhmmss, price, vol))
        # 봉 경계 진입으로 flush
        trader.on_price(
            "005930", FakePrice("005930", "093530", 70_200, 30)
        )
        bars = trader.bar_buffer.get("005930")
        print(f"    accumulated_5m_bars={len(bars)}\n")

        print("[4] on_fill — 매수 체결 시뮬")
        trader.on_fill(FakeFillNotice(side="02"))
        pos = trader.tracker.get_position("005930")
        print(
            f"    position qty={pos.qty if pos else 0} "
            f"avg={int(pos.avg_price) if pos else 0}\n"
        )

        print("[5] on_fill — 매도 체결 시뮬 (수익 종료)")
        trader.on_fill(
            FakeFillNotice(
                side="01",
                fill_qty=71,
                fill_price=71_000,
                order_no="DEMO-2",
                fill_time="2026-05-07T10:30:00",
            )
        )
        print(
            f"    realized_pnl={int(trader.tracker.state.realized_pnl_krw)} "
            f"halted={trader.killswitch.is_halted()}\n"
        )

        print("[6] shutdown — bar buffer snapshot + DAILY 알림")
        trader.shutdown(asof)
        print("[DEMO] complete")


if __name__ == "__main__":
    main()
