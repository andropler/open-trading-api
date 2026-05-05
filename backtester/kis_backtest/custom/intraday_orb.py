"""KR 5분봉 Opening Range Breakout (ORB) 백테스터.

⚠️ BASELINE ONLY — 단독 운용 부적합.
    VALIDATION.md(v1) §4 / VALIDATION_v2.md §5 검증 결과 cost 0.30% 이상에서 손실 전략이며,
    한국 실비용 0.4~0.5% 환경에서 PF 0.5~0.7로 명백한 마이너스. 시기 의존성도 큼
    (2025 PF 0.77 vs 2026 PF 2.64).
    본 모듈은 5m Composite의 `orb_event_quality` 패밀리(이벤트 거래대금 + regime 필터 결합 시 PF 2+)
    비교용 baseline / 후속 연구용으로만 사용한다.

5분봉 데이터를 로컬 parquet에서 직접 읽어 vectorized 시뮬레이션을 수행한다.
LeanClient를 우회하므로 1554종목 sweep도 합리적 시간에 가능.

전략:
    - 매일 9:00~9:30 (6개 5분봉) = Opening Range
    - 10:00~10:30 첫 5분봉 close가 ORB.high 돌파 시 매수
    - 전일 거래대금 상위 10종목, OR 폭, 갭 필터로 false breakout 회피
    - 청산:
        (a) 가격이 entry × (1 - sl_pct/100) 이하로 하락 → stop loss
        (b) 가격이 entry × (1 + tp_pct/100) 이상으로 상승 → take profit
        (c) `exit_hh:mm` 도달 → 종가 시간 청산 (디폴트 14:30)
    - 일별 거래대금 상위 N개 종목만 진입 후보 (전일 기준 = no look-ahead)
    - 동시 보유 max_positions 제한

레퍼런스:
    `kr_intraday_breakout.py` (V4.1 1H baseline) 의 패턴을 단순화.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ORBParams:
    """ORB 파라미터."""

    or_minutes: int = 30        # ORB 윈도우 길이 (분) — 5분봉 6개 = 30분
    sl_pct: float = 3.0         # 손절 (entry * (1 - sl_pct/100))
    tp_pct: float = 8.0         # 익절 (entry * (1 + tp_pct/100))
    entry_window_start: str = "10:00"  # 진입 시작 시각
    entry_window_end: str = "10:30"    # 진입 마감 시각
    exit_time: str = "14:30"           # 시간 청산
    top_n_stocks: int = 10      # 일별 거래대금 상위 N
    ranking_window: int = 5     # 거래대금 평균 윈도우 (전일까지)
    cost_pct: float = 0.20      # 편도 거래비용 % (수수료+슬리피지+세)
    min_price: float = 3000.0   # 최소 진입가 (저가주 필터)
    min_gap_pct: float = -5.0   # 당일 시가 갭 하한 (%)
    max_gap_pct: float = 20.0   # 당일 시가 갭 상한 (%)
    max_or_width_pct: float = 8.0  # opening range 폭 상한 (%)
    volume_avg_window: int = 80    # 돌파봉 거래량 pace 기준 lookback bars
    min_volume_ratio: float = 0.0  # 돌파봉 거래량 / 직전 평균 거래량. 0이면 비활성화.


@dataclass
class ORBTrade:
    entry_date: date
    exit_date: date
    ticker: str
    entry_price: float
    exit_price: float
    entry_time: dtime
    exit_time: dtime
    exit_reason: str
    gross_pnl_pct: float
    net_pnl_krw: float
    shares: int
    position_size: float
    rank: int = 0
    gap_pct: float = 0.0
    or_width_pct: float = 0.0
    volume_ratio: float = 0.0

    @property
    def net_pnl_pct(self) -> float:
        if self.position_size <= 0:
            return 0.0
        return (self.net_pnl_krw / self.position_size) * 100.0


def _detect_default_5m_dir() -> Path:
    candidates = [
        Path("/Users/benjamin/personal_workspace/shared_data/kr_stocks/5m"),
        Path.cwd() / "data" / "kr_stocks" / "5m",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _parse_hhmm(s: str) -> dtime:
    parts = s.split(":")
    return dtime(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


class IntradayORBBacktester:
    STRATEGY_ID = "kr_intraday_orb_5m"
    STRATEGY_NAME = "KR 5min Opening Range Breakout"

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        params: Optional[ORBParams] = None,
        symbols: Optional[Sequence[str]] = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else _detect_default_5m_dir()
        if not self.data_dir.exists():
            raise FileNotFoundError(f"5m parquet dir not found: {self.data_dir}")
        self.params = params or ORBParams()
        self._symbol_filter: Optional[set[str]] = set(symbols) if symbols else None
        self.raw_data: Dict[str, pd.DataFrame] = {}
        self.daily_ranked: Dict[date, list[tuple[str, float]]] = {}
        self.signals_per_day: Dict[date, list[Tuple[date, dtime, str, float, dict]]] = {}
        self.trades: list[ORBTrade] = []
        self.initial_equity: float = 0.0
        self.final_equity: float = 0.0
        self.last_run_seconds: float = 0.0

    # ------------------------------------------------------------------
    # 데이터 로드
    # ------------------------------------------------------------------

    def load_data(self) -> "IntradayORBBacktester":
        self.raw_data = {}
        for filename in sorted(os.listdir(self.data_dir)):
            if not filename.endswith(".parquet"):
                continue
            ticker = filename.replace("_5m.parquet", "").split(".")[0]
            if self._symbol_filter and ticker not in self._symbol_filter:
                continue
            path = self.data_dir / filename
            df = pd.read_parquet(path)
            if df.empty or "timestamp" not in df.columns:
                continue
            df = df.copy()
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            required = {"open", "high", "low", "close", "volume"}
            if not required.issubset(df.columns):
                continue
            df = df.sort_values("timestamp").reset_index(drop=True)
            self.raw_data[ticker] = df
        if not self.raw_data:
            raise ValueError(f"No usable 5m parquet in {self.data_dir}")
        logger.info("loaded %d tickers from %s", len(self.raw_data), self.data_dir)
        return self

    # ------------------------------------------------------------------
    # 일별 거래대금 랭킹 (전일 N일 평균, no look-ahead)
    # ------------------------------------------------------------------

    def compute_rankings(self) -> "IntradayORBBacktester":
        if not self.raw_data:
            raise ValueError("call load_data() first")

        per_day_tv: dict[date, dict[str, float]] = {}
        for ticker, df in self.raw_data.items():
            tmp = pd.DataFrame({
                "date": df["timestamp"].dt.date,
                "tv": df["close"].astype(float) * df["volume"].astype(float),
            })
            day_sum = tmp.groupby("date")["tv"].sum()
            for current_date, val in day_sum.items():
                if val <= 0 or pd.isna(val):
                    continue
                per_day_tv.setdefault(current_date, {})[ticker] = float(val)

        rolling_window = self.params.ranking_window
        sorted_dates = sorted(per_day_tv.keys())
        history: dict[str, list[float]] = {}

        self.daily_ranked = {}
        for d in sorted_dates:
            avg_tv: dict[str, float] = {}
            for ticker, hist in history.items():
                if len(hist) >= 1:
                    avg_tv[ticker] = float(np.mean(hist[-rolling_window:]))
            ranked = sorted(avg_tv.items(), key=lambda kv: kv[1], reverse=True)
            self.daily_ranked[d] = ranked

            for ticker, tv in per_day_tv[d].items():
                history.setdefault(ticker, []).append(tv)
                if len(history[ticker]) > rolling_window:
                    history[ticker] = history[ticker][-rolling_window:]
        return self

    # ------------------------------------------------------------------
    # 신호 사전 계산 (entry-only). 일별 종목별로 1개 신호 최대.
    # ------------------------------------------------------------------

    def precompute(self) -> "IntradayORBBacktester":
        if not self.daily_ranked:
            raise ValueError("call compute_rankings() first")

        p = self.params
        or_bars = max(1, p.or_minutes // 5)
        ew_start = _parse_hhmm(p.entry_window_start)
        ew_end = _parse_hhmm(p.entry_window_end)
        ex_t = _parse_hhmm(p.exit_time)

        # 종목별 일별 인덱스 미리 분해
        self.signals_per_day = {}

        for ticker, df in self.raw_data.items():
            df2 = df.copy()
            df2["date"] = df2["timestamp"].dt.date
            df2["t"] = df2["timestamp"].dt.time
            df2["hhmm"] = df2["timestamp"].dt.hour * 100 + df2["timestamp"].dt.minute
            df2["avg_volume"] = (
                df2["volume"]
                .replace(0, np.nan)
                .shift(1)
                .rolling(p.volume_avg_window, min_periods=max(20, p.volume_avg_window // 4))
                .mean()
            )
            prev_close = df2.groupby("date")["close"].last().sort_index().shift(1).to_dict()
            for current_date, group in df2.groupby("date"):
                g = group.sort_values("timestamp").reset_index(drop=True)
                if len(g) < or_bars + 4:
                    continue
                # ORB high/low
                or_part = g.iloc[:or_bars]
                or_high = float(or_part["high"].max())
                or_low = float(or_part["low"].min())
                if or_high <= 0:
                    continue
                or_width_pct = (or_high / or_low - 1) * 100 if or_low > 0 else float("inf")
                if or_width_pct > p.max_or_width_pct:
                    continue

                prior_close = prev_close.get(current_date)
                if prior_close is None or pd.isna(prior_close) or prior_close <= 0:
                    continue
                day_open = float(g["open"].iloc[0])
                gap_pct = (day_open / float(prior_close) - 1) * 100
                if gap_pct < p.min_gap_pct or gap_pct > p.max_gap_pct:
                    continue

                # 당일 최초 OR 돌파가 진입 윈도우 안에서 발생한 경우만 사용한다.
                # 09:30~10:00 조기 돌파 후 되밀린 종목은 이 전략의 주된 손실원이었다.
                post = g.iloc[or_bars:]
                candidates = post[post["close"] > or_high]
                if candidates.empty:
                    continue
                first = candidates.iloc[0]
                if first["t"] < ew_start or first["t"] > ew_end:
                    continue
                entry_price = float(first["close"])
                if entry_price < p.min_price:
                    continue
                avg_volume = float(first["avg_volume"]) if pd.notna(first["avg_volume"]) else 0.0
                volume_ratio = float(first["volume"] / avg_volume) if avg_volume > 0 else 0.0
                if p.min_volume_ratio > 0 and volume_ratio < p.min_volume_ratio:
                    continue

                # 후속 5분봉 (청산 시뮬레이션용)
                after = g[g["timestamp"] > first["timestamp"]]
                # 14:30 종가 시간 청산 — 그 이후 봉은 무시
                after = after[after["t"] <= ex_t]

                bundle = {
                    "or_high": or_high,
                    "or_low": or_low,
                    "entry_price": entry_price,
                    "entry_time": first["t"],
                    "entry_hhmm": int(first["hhmm"]),
                    "gap_pct": gap_pct,
                    "or_width_pct": or_width_pct,
                    "volume_ratio": volume_ratio,
                    "after_high": after["high"].astype(float).to_numpy(),
                    "after_low": after["low"].astype(float).to_numpy(),
                    "after_close": after["close"].astype(float).to_numpy(),
                    "after_time": after["t"].to_numpy(),
                }
                self.signals_per_day.setdefault(current_date, []).append(
                    (current_date, first["t"], ticker, entry_price, bundle)
                )

        # 각 날짜 신호를 entry_time 오름차순으로 정렬
        for d in self.signals_per_day:
            rank_map = {ticker: idx for idx, (ticker, _) in enumerate(self.daily_ranked.get(d, []), start=1)}
            self.signals_per_day[d].sort(
                key=lambda row: (
                    row[1],
                    rank_map.get(row[2], 9999),
                    -float(row[4].get("volume_ratio", 0.0)),
                )
            )
        return self

    # ------------------------------------------------------------------
    # 시뮬레이션
    # ------------------------------------------------------------------

    def run(
        self,
        initial_equity: float = 100_000_000,
        max_positions: int = 3,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> "IntradayORBBacktester":
        if not self.signals_per_day:
            raise ValueError("call precompute() first")
        p = self.params
        sl = p.sl_pct / 100.0
        tp = p.tp_pct / 100.0
        cost = p.cost_pct / 100.0

        sorted_dates = sorted(self.signals_per_day.keys())
        if start_date:
            sorted_dates = [d for d in sorted_dates if d >= start_date]
        if end_date:
            sorted_dates = [d for d in sorted_dates if d <= end_date]

        equity = initial_equity
        self.initial_equity = initial_equity
        self.trades = []
        t0 = time.perf_counter()

        for current_date in sorted_dates:
            ranked = self.daily_ranked.get(current_date, [])
            rank_map = {ticker: idx for idx, (ticker, _) in enumerate(ranked, start=1)}
            top_set = {ticker for ticker, _ in ranked[: p.top_n_stocks]}
            if not top_set:
                continue
            day_signals = self.signals_per_day[current_date]
            day_signals = [s for s in day_signals if s[2] in top_set]

            opened_today: list[str] = []
            for sig_date, entry_time, ticker, entry_price, bundle in day_signals:
                if len(opened_today) >= max_positions:
                    break
                if equity < entry_price:
                    continue
                allocation = equity / max(1, max_positions - len(opened_today))
                shares = int(allocation / entry_price)
                if shares <= 0:
                    continue
                position_size = shares * entry_price
                equity -= position_size

                # 청산 simulation: stop / take / timestop
                stop_price = entry_price * (1 - sl)
                take_price = entry_price * (1 + tp)
                exit_price = None
                exit_reason = "time_stop"
                exit_time = entry_time
                ah = bundle["after_high"]
                al = bundle["after_low"]
                ac = bundle["after_close"]
                at = bundle["after_time"]

                for i in range(len(ah)):
                    # 5분봉 high/low로 stop/take 체크 (low 먼저 — 보수적)
                    if al[i] <= stop_price:
                        exit_price = stop_price
                        exit_reason = "stop_loss"
                        exit_time = at[i]
                        break
                    if ah[i] >= take_price:
                        exit_price = take_price
                        exit_reason = "take_profit"
                        exit_time = at[i]
                        break
                if exit_price is None:
                    if len(ac) > 0:
                        exit_price = float(ac[-1])
                        exit_time = at[-1]
                    else:
                        exit_price = entry_price
                        exit_time = entry_time

                gross_pnl_pct = (exit_price - entry_price) / entry_price
                # 거래비용: 매수 매도 양방향
                net_pnl_pct = gross_pnl_pct - 2 * cost
                net_pnl_krw = net_pnl_pct * position_size
                equity += position_size + net_pnl_krw

                self.trades.append(ORBTrade(
                    entry_date=sig_date,
                    exit_date=sig_date,
                    ticker=ticker,
                    entry_price=entry_price,
                    exit_price=float(exit_price),
                    entry_time=entry_time,
                    exit_time=exit_time if hasattr(exit_time, "hour") else entry_time,
                    exit_reason=exit_reason,
                    gross_pnl_pct=gross_pnl_pct * 100,
                    net_pnl_krw=net_pnl_krw,
                    shares=shares,
                    position_size=position_size,
                    rank=rank_map.get(ticker, 0),
                    gap_pct=float(bundle.get("gap_pct", 0.0)),
                    or_width_pct=float(bundle.get("or_width_pct", 0.0)),
                    volume_ratio=float(bundle.get("volume_ratio", 0.0)),
                ))
                opened_today.append(ticker)

        self.final_equity = equity
        self.last_run_seconds = time.perf_counter() - t0
        return self

    # ------------------------------------------------------------------
    # 메트릭
    # ------------------------------------------------------------------

    def metrics(self) -> Dict[str, float]:
        n = len(self.trades)
        wins = sum(1 for t in self.trades if t.net_pnl_krw > 0)
        losses = n - wins
        gross_win = sum(t.net_pnl_krw for t in self.trades if t.net_pnl_krw > 0)
        gross_loss = -sum(t.net_pnl_krw for t in self.trades if t.net_pnl_krw <= 0)
        ret_pct = (self.final_equity - self.initial_equity) / self.initial_equity if self.initial_equity > 0 else 0
        # daily equity for sharpe/mdd
        if not self.trades:
            return {
                "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "total_return_pct": 0.0, "cagr": 0.0, "sharpe_ratio": 0.0,
                "max_drawdown": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
                "duration_seconds": self.last_run_seconds,
            }
        # daily PnL series → equity curve
        df = pd.DataFrame([{
            "date": t.exit_date,
            "pnl": t.net_pnl_krw,
        } for t in self.trades])
        daily_pnl = df.groupby("date")["pnl"].sum().sort_index()
        equity_curve = self.initial_equity + daily_pnl.cumsum()
        # extend to all trading dates by forward-fill
        all_dates = pd.date_range(equity_curve.index.min(), equity_curve.index.max(), freq="B")
        equity_curve = equity_curve.reindex(all_dates.date).ffill()
        daily_returns = equity_curve.pct_change().dropna()
        sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252)) if daily_returns.std() > 0 else 0.0
        rolling_max = equity_curve.cummax()
        mdd = float(((equity_curve - rolling_max) / rolling_max).min())
        years = max(0.001, (equity_curve.index[-1] - equity_curve.index[0]).days / 365.25)
        cagr = float((equity_curve.iloc[-1] / self.initial_equity) ** (1 / years) - 1)

        return {
            "total_trades": n,
            "win_rate": wins / n if n else 0.0,
            "profit_factor": gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
            "total_return_pct": float(ret_pct),
            "cagr": cagr,
            "sharpe_ratio": sharpe,
            "max_drawdown": abs(mdd),
            # gross_pnl_pct 는 이미 % 단위 (run()에서 *100 적용). 0~1 단위로 정규화해 다른 메트릭과 통일.
            "avg_win_pct": float(np.mean([t.gross_pnl_pct for t in self.trades if t.net_pnl_krw > 0]) / 100.0) if wins else 0.0,
            "avg_loss_pct": float(np.mean([t.gross_pnl_pct for t in self.trades if t.net_pnl_krw <= 0]) / 100.0) if losses else 0.0,
            "duration_seconds": self.last_run_seconds,
        }
