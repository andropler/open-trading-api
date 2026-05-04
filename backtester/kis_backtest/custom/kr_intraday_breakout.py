"""KR 1H Breakout V4.1 custom backtester.

This module ports the validated alpha-hunter Korean intraday breakout logic
into the open-trading-api backtester structure without depending on the
alpha-hunter package at runtime.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from ..models import BacktestResult, Order, OrderSide, OrderStatus, OrderType


def detect_default_parquet_data_dir() -> Path:
    """Best-effort discovery of the sibling alpha-hunter KR 1H dataset."""
    candidates = [
        Path.cwd() / "data" / "kr_stocks" / "1h",
        Path(__file__).resolve().parents[4] / "alpha-hunter" / "data" / "kr_stocks" / "1h",
        Path(__file__).resolve().parents[2] / "data" / "kr_stocks" / "1h",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[1]


def _as_date(value: date | datetime | str | None) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


@dataclass
class BreakoutV41Params:
    """Validated defaults from alpha-hunter KR 1H Breakout V4.1."""

    breakout_lookback: int = 4
    vol_multiplier: float = 2.0
    vol_avg_window: int = 20
    sl_pct: float = 5.0
    trail_pct: float = 0.5
    trail_activation: float = 0.5
    top_n_stocks: int = 15
    ranking_window: int = 5
    entry_hour_start: int = 10
    entry_hour_end: int = 11
    exit_hour: int = 14
    max_hold_days: int = 1
    require_vwap: bool = True
    require_bullish_bar: bool = True
    breakout_margin: float = 0.0
    min_price: float = 5000.0
    cost_pct: float = 0.55


@dataclass
class BreakoutTrade:
    """Completed trade record."""

    entry_date: date
    exit_date: date
    ticker: str
    entry_price: float
    exit_price: float
    entry_hour: int
    exit_hour: int
    exit_reason: str
    gross_pnl_pct: float
    net_pnl_krw: float
    hold_bars: int = 0
    shares: int = 0
    position_size: float = 0.0

    @property
    def net_pnl_pct(self) -> float:
        if self.position_size <= 0:
            return 0.0
        return (self.net_pnl_krw / self.position_size) * 100.0


class KRIntradayBreakoutV41Backtester:
    """Standalone backtester for KR 1H Breakout V4.1."""

    STRATEGY_ID = "kr_intraday_breakout_v41"
    STRATEGY_NAME = "KR 1H Breakout V4.1"

    def __init__(
        self,
        data_dir: str | Path | None = None,
        params: Optional[BreakoutV41Params] = None,
    ):
        self.data_dir = Path(data_dir) if data_dir is not None else detect_default_parquet_data_dir()
        self.params = params or BreakoutV41Params()
        self.raw_data: Dict[str, pd.DataFrame] = {}
        self.daily_ranked: Dict[date, list[tuple[str, float]]] = {}
        self.stock_days: Dict[str, Dict[date, dict[str, np.ndarray]]] = {}
        self.stock_all_days: Dict[str, list[date]] = {}
        self.stock_all_day_bars: Dict[str, Dict[date, dict[str, np.ndarray]]] = {}
        self.trades: list[BreakoutTrade] = []
        self.initial_equity: float = 0.0
        self.final_equity: float = 0.0
        self.last_run_seconds: float = 0.0
        self.last_artifacts: dict[str, str] = {}

    def load_data(self) -> "KRIntradayBreakoutV41Backtester":
        """Load sibling alpha-hunter parquet data."""
        if not self.data_dir.exists():
            raise FileNotFoundError(f"KR 1H parquet data directory not found: {self.data_dir}")

        self.raw_data = {}
        for filename in os.listdir(self.data_dir):
            if not filename.endswith(".parquet"):
                continue
            path = self.data_dir / filename
            ticker = path.stem.replace("_1h", "")
            df = pd.read_parquet(path)
            if df.empty:
                continue

            if "timestamp" not in df.columns:
                raise ValueError(f"Missing timestamp column in {path}")

            normalized = df.copy()
            normalized["timestamp"] = pd.to_datetime(normalized["timestamp"])
            required = {"open", "high", "low", "close", "volume"}
            missing = required.difference(normalized.columns)
            if missing:
                raise ValueError(f"Missing columns {sorted(missing)} in {path}")

            normalized = normalized.sort_values("timestamp").reset_index(drop=True)
            self.raw_data[ticker] = normalized

        if not self.raw_data:
            raise ValueError(f"No parquet files found in {self.data_dir}")
        return self

    def compute_rankings(self) -> "KRIntradayBreakoutV41Backtester":
        """Compute no-look-ahead daily top-volume ranking."""
        if not self.raw_data:
            raise ValueError("No raw data loaded")

        daily_tv: dict[date, dict[str, float]] = {}

        for ticker, df in self.raw_data.items():
            df2 = df.copy()
            df2["date"] = df2["timestamp"].dt.date
            df2 = df2[df2["timestamp"].dt.hour >= 10]

            for current_date, group in df2.groupby("date"):
                tv = float((group["close"] * group["volume"]).sum())
                if tv <= 0:
                    continue
                daily_tv.setdefault(current_date, {})[ticker] = tv

        if not daily_tv:
            raise ValueError("Unable to compute rankings from loaded data")

        all_dates = sorted(daily_tv.keys())
        all_tickers = sorted({ticker for daily in daily_tv.values() for ticker in daily})

        tv_matrix = pd.DataFrame(0.0, index=all_dates, columns=all_tickers)
        for current_date, ticker_map in daily_tv.items():
            for ticker, trading_value in ticker_map.items():
                tv_matrix.at[current_date, ticker] = trading_value

        rolling_tv = tv_matrix.shift(1).rolling(self.params.ranking_window, min_periods=1).mean()
        self.daily_ranked = {}
        for current_date in all_dates:
            row = rolling_tv.loc[current_date]
            row = row[row > 0].sort_values(ascending=False)
            self.daily_ranked[current_date] = [(ticker, float(value)) for ticker, value in row.items()]

        return self

    def precompute(self) -> "KRIntradayBreakoutV41Backtester":
        """Precompute per-day indicator arrays for fast simulation."""
        if not self.daily_ranked:
            raise ValueError("Rankings must be computed before precompute()")

        top_days: dict[str, set[date]] = {}
        for current_date, ranked in self.daily_ranked.items():
            for ticker, _ in ranked[: self.params.top_n_stocks]:
                top_days.setdefault(ticker, set()).add(current_date)

        self.stock_days = {}
        self.stock_all_days = {}
        self.stock_all_day_bars = {}

        for ticker, raw_df in self.raw_data.items():
            if ticker not in top_days:
                continue

            df = raw_df.copy()
            df["date"] = df["timestamp"].dt.date
            df["hour"] = df["timestamp"].dt.hour

            vol_series = df["volume"].replace(0, np.nan)
            df["avg_volume"] = vol_series.shift(1).rolling(
                self.params.vol_avg_window,
                min_periods=5,
            ).mean()

            df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
            df["tp_vol"] = df["typical_price"] * df["volume"]
            df["cum_tp_vol"] = df.groupby("date")["tp_vol"].cumsum()
            df["cum_vol"] = df.groupby("date")["volume"].cumsum()
            df["vwap"] = df["cum_tp_vol"] / df["cum_vol"].replace(0, np.nan)
            df["prev_n_high"] = df["high"].shift(1).rolling(self.params.breakout_lookback).max()

            self.stock_all_days[ticker] = sorted(df["date"].unique())
            all_day_bars: dict[date, dict[str, np.ndarray]] = {}

            for current_date, group in df.groupby("date"):
                g = group.sort_values("timestamp")
                if len(g) < 3:
                    continue
                all_day_bars[current_date] = {
                    "hour": g["hour"].to_numpy(),
                    "open": g["open"].astype(float).to_numpy(),
                    "high": g["high"].astype(float).to_numpy(),
                    "low": g["low"].astype(float).to_numpy(),
                    "close": g["close"].astype(float).to_numpy(),
                    "volume": g["volume"].astype(float).to_numpy(),
                    "avg_volume": g["avg_volume"].astype(float).to_numpy(),
                    "vwap": g["vwap"].astype(float).to_numpy(),
                    "prev_n_high": g["prev_n_high"].astype(float).to_numpy(),
                }

            self.stock_all_day_bars[ticker] = all_day_bars
            eligible_days = {d: bars for d, bars in all_day_bars.items() if d in top_days[ticker]}
            if eligible_days:
                self.stock_days[ticker] = eligible_days

        return self

    def run(
        self,
        initial_equity: float = 10_000_000,
        max_positions: int = 3,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
    ) -> "KRIntradayBreakoutV41Backtester":
        """Run the backtest."""
        if not self.stock_days:
            raise ValueError("precompute() must run before run()")

        start_time = time.perf_counter()
        p = self.params

        requested_start = _as_date(start_date)
        requested_end = _as_date(end_date)
        known_dates = sorted(self.daily_ranked.keys())
        if requested_start is None:
            requested_start = known_dates[0]
        if requested_end is None:
            requested_end = known_dates[-1]
        if requested_start > requested_end:
            raise ValueError("start_date must be earlier than or equal to end_date")

        sl_pct = p.sl_pct / 100.0
        trail_pct = p.trail_pct / 100.0
        trail_activation = p.trail_activation / 100.0
        cost_pct = p.cost_pct / 100.0

        eligible = {
            (current_date, ticker)
            for current_date, ranked in self.daily_ranked.items()
            for ticker, _ in ranked[: p.top_n_stocks]
        }

        raw_signals: list[tuple[date, int, str, float, float, int]] = []
        for ticker, days_dict in self.stock_days.items():
            sorted_dates = sorted(days_dict.keys())
            all_bars: list[tuple[date, int, float, float, float, float, float, float, float, float]] = []

            for current_date in sorted_dates:
                day_bars = days_dict[current_date]
                for idx in range(len(day_bars["hour"])):
                    all_bars.append(
                        (
                            current_date,
                            int(day_bars["hour"][idx]),
                            float(day_bars["open"][idx]),
                            float(day_bars["high"][idx]),
                            float(day_bars["low"][idx]),
                            float(day_bars["close"][idx]),
                            float(day_bars["volume"][idx]),
                            float(day_bars["avg_volume"][idx]),
                            float(day_bars["vwap"][idx]),
                            float(day_bars["prev_n_high"][idx]),
                        )
                    )

            raw_df = self.raw_data[ticker]
            raw_dates = raw_df["timestamp"].dt.date.to_numpy()
            raw_hours = raw_df["timestamp"].dt.hour.to_numpy()
            raw_keys = list(zip(raw_dates.tolist(), raw_hours.tolist()))
            raw_key_to_index = {key: idx for idx, key in enumerate(raw_keys)}

            for bar in all_bars:
                bar_date, hour, bar_open, bar_high, _bar_low, bar_close, bar_vol, avg_vol, bar_vwap, prev_high = bar

                if bar_date < requested_start or bar_date > requested_end:
                    continue
                if (bar_date, ticker) not in eligible:
                    continue
                if hour < p.entry_hour_start or hour > p.entry_hour_end:
                    continue
                if np.isnan(prev_high) or np.isnan(avg_vol) or avg_vol <= 0:
                    continue
                if bar_high <= prev_high * (1 + p.breakout_margin):
                    continue
                if bar_vol <= 0 or bar_vol < p.vol_multiplier * avg_vol:
                    continue
                if p.require_vwap and (np.isnan(bar_vwap) or bar_close <= bar_vwap):
                    continue
                if p.require_bullish_bar and bar_close <= bar_open:
                    continue
                if p.min_price > 0 and bar_close < p.min_price:
                    continue

                raw_bar_idx = raw_key_to_index.get((bar_date, hour), -1)
                next_bar_idx = raw_bar_idx + 1 if raw_bar_idx >= 0 else -1
                if next_bar_idx <= 0 or next_bar_idx >= len(raw_keys):
                    continue
                next_open = float(raw_df["open"].iloc[next_bar_idx])
                if next_open <= 0 or np.isnan(next_open):
                    continue
                entry_hour = int(raw_hours[next_bar_idx])
                raw_signals.append((bar_date, entry_hour, ticker, next_open, bar_high, next_bar_idx + 1))

        if self.raw_data:
            last_known_date = max(df["timestamp"].dt.date.max() for df in self.raw_data.values())
        else:
            last_known_date = requested_end
        raw_signals.append((last_known_date, 99, "__END__", 0.0, 0.0, 0))
        raw_signals.sort(key=lambda item: (item[0], item[1]))

        self.trades = []
        self.initial_equity = initial_equity
        equity = initial_equity
        open_positions: dict[str, dict[str, object]] = {}

        for signal_date, signal_hour, ticker, entry_price, _entry_high, exit_start_idx in raw_signals:
            for held_ticker in list(open_positions.keys()):
                position = open_positions[held_ticker]
                raw_df = self.raw_data[held_ticker]
                raw_dates = raw_df["timestamp"].dt.date.to_numpy()
                raw_hours = raw_df["timestamp"].dt.hour.to_numpy()
                raw_highs = raw_df["high"].astype(float).to_numpy()
                raw_lows = raw_df["low"].astype(float).to_numpy()
                raw_closes = raw_df["close"].astype(float).to_numpy()

                scan_idx = int(position["scan_idx"])
                exited = False
                while scan_idx < len(raw_df):
                    current_date = raw_dates[scan_idx]
                    current_hour = int(raw_hours[scan_idx])

                    if current_date > signal_date or (current_date == signal_date and current_hour > signal_hour):
                        position["scan_idx"] = scan_idx
                        break

                    if current_date > position["last_allowed_date"]:
                        if scan_idx > int(position["scan_idx"]):
                            exit_price = float(raw_closes[scan_idx - 1])
                            exit_hour = int(raw_hours[scan_idx - 1])
                            exit_date = raw_dates[scan_idx - 1]
                        else:
                            exit_price = float(position["entry_price"])
                            exit_hour = int(position["entry_hour"])
                            exit_date = position["entry_date"]
                        equity += float(position["position_size"]) + self._finalize_trade(
                            ticker=held_ticker,
                            entry_date=position["entry_date"],
                            exit_date=exit_date,
                            entry_price=float(position["entry_price"]),
                            exit_price=exit_price,
                            entry_hour=int(position["entry_hour"]),
                            exit_hour=exit_hour,
                            exit_reason="time_stop",
                            hold_bars=int(position["bars_held"]),
                            shares=int(position["shares"]),
                            position_size=float(position["position_size"]),
                            cost_pct=cost_pct,
                        )
                        del open_positions[held_ticker]
                        exited = True
                        break

                    current_high = float(raw_highs[scan_idx])
                    current_low = float(raw_lows[scan_idx])
                    current_close = float(raw_closes[scan_idx])
                    position["bars_held"] = int(position["bars_held"]) + 1

                    if current_low <= float(position["stop_price"]):
                        exit_price = float(position["stop_price"])
                        exit_reason = "trailing_stop" if bool(position["trailing_active"]) else "stop_loss"
                        equity += float(position["position_size"]) + self._finalize_trade(
                            ticker=held_ticker,
                            entry_date=position["entry_date"],
                            exit_date=current_date,
                            entry_price=float(position["entry_price"]),
                            exit_price=exit_price,
                            entry_hour=int(position["entry_hour"]),
                            exit_hour=current_hour,
                            exit_reason=exit_reason,
                            hold_bars=int(position["bars_held"]),
                            shares=int(position["shares"]),
                            position_size=float(position["position_size"]),
                            cost_pct=cost_pct,
                        )
                        del open_positions[held_ticker]
                        exited = True
                        break

                    if current_high > float(position["max_price"]):
                        position["max_price"] = current_high
                    if current_close / float(position["entry_price"]) - 1 >= trail_activation and not bool(position["trailing_active"]):
                        position["trailing_active"] = True
                    if bool(position["trailing_active"]):
                        new_stop = float(position["max_price"]) * (1 - trail_pct)
                        if new_stop > float(position["stop_price"]):
                            position["stop_price"] = new_stop

                    if current_date == position["last_allowed_date"] and current_hour >= p.exit_hour:
                        exit_price = current_close
                        equity += float(position["position_size"]) + self._finalize_trade(
                            ticker=held_ticker,
                            entry_date=position["entry_date"],
                            exit_date=current_date,
                            entry_price=float(position["entry_price"]),
                            exit_price=exit_price,
                            entry_hour=int(position["entry_hour"]),
                            exit_hour=current_hour,
                            exit_reason="time_stop",
                            hold_bars=int(position["bars_held"]),
                            shares=int(position["shares"]),
                            position_size=float(position["position_size"]),
                            cost_pct=cost_pct,
                        )
                        del open_positions[held_ticker]
                        exited = True
                        break

                    scan_idx += 1

                if not exited and held_ticker in open_positions:
                    open_positions[held_ticker]["scan_idx"] = scan_idx

            if ticker == "__END__":
                continue
            if ticker in open_positions:
                continue
            if len(open_positions) >= max_positions:
                continue

            deployed = sum(float(position["position_size"]) for position in open_positions.values())
            total_assets = equity + deployed
            allocation = min(total_assets / max_positions, equity)
            if allocation < entry_price:
                continue

            shares = int(allocation / entry_price)
            if shares <= 0:
                continue

            position_size = shares * entry_price
            all_trading_dates = self.stock_all_days.get(ticker, [])
            date_lookup = {current_date: idx for idx, current_date in enumerate(all_trading_dates)}
            current_idx = date_lookup.get(signal_date, -1)
            if current_idx >= 0:
                deadline_idx = min(current_idx + p.max_hold_days, len(all_trading_dates) - 1)
                last_allowed_date = all_trading_dates[deadline_idx]
            else:
                last_allowed_date = signal_date

            equity -= position_size
            open_positions[ticker] = {
                "entry_price": entry_price,
                "entry_date": signal_date,
                "entry_hour": signal_hour,
                "stop_price": entry_price * (1 - sl_pct),
                "max_price": entry_price,
                "trailing_active": False,
                "bars_held": 0,
                "scan_idx": exit_start_idx,
                "last_allowed_date": last_allowed_date,
                "shares": shares,
                "position_size": position_size,
            }

        self.final_equity = equity
        self.last_run_seconds = time.perf_counter() - start_time
        self.trades.sort(key=lambda trade: (trade.entry_date, trade.entry_hour, trade.ticker))
        return self

    def _finalize_trade(
        self,
        *,
        ticker: str,
        entry_date: date,
        exit_date: date,
        entry_price: float,
        exit_price: float,
        entry_hour: int,
        exit_hour: int,
        exit_reason: str,
        hold_bars: int,
        shares: int,
        position_size: float,
        cost_pct: float,
    ) -> float:
        gross_pnl_pct = (exit_price / entry_price - 1) * 100.0
        net_pnl_krw = shares * (exit_price - entry_price) - position_size * cost_pct
        self.trades.append(
            BreakoutTrade(
                entry_date=entry_date,
                exit_date=exit_date,
                ticker=ticker,
                entry_price=entry_price,
                exit_price=exit_price,
                entry_hour=entry_hour,
                exit_hour=exit_hour,
                exit_reason=exit_reason,
                gross_pnl_pct=gross_pnl_pct,
                net_pnl_krw=net_pnl_krw,
                hold_bars=hold_bars,
                shares=shares,
                position_size=position_size,
            )
        )
        return net_pnl_krw

    def get_results_df(self) -> pd.DataFrame:
        """Return completed trades as a DataFrame."""
        if not self.trades:
            return pd.DataFrame()

        return pd.DataFrame(
            [
                {
                    "entry_date": trade.entry_date,
                    "exit_date": trade.exit_date,
                    "ticker": trade.ticker,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "entry_hour": trade.entry_hour,
                    "exit_hour": trade.exit_hour,
                    "exit_reason": trade.exit_reason,
                    "gross_pnl_pct": trade.gross_pnl_pct,
                    "net_pnl_pct": trade.net_pnl_pct,
                    "net_pnl_krw": trade.net_pnl_krw,
                    "hold_bars": trade.hold_bars,
                    "shares": trade.shares,
                    "position_size": trade.position_size,
                }
                for trade in self.trades
            ]
        )

    def build_equity_curve(
        self,
        initial_equity: float,
        start_date: date | datetime | str,
        end_date: date | datetime | str,
    ) -> pd.Series:
        """Build a realized-PnL daily equity curve."""
        start_dt = pd.Timestamp(_as_date(start_date))
        end_dt = pd.Timestamp(_as_date(end_date))
        if self.trades:
            last_exit = max(pd.Timestamp(trade.exit_date) for trade in self.trades)
            curve_end = max(end_dt, last_exit)
        else:
            curve_end = end_dt

        index = pd.bdate_range(start_dt, curve_end)
        if index.empty:
            index = pd.DatetimeIndex([start_dt])

        realized = pd.Series(0.0, index=index)
        for trade in self.trades:
            exit_ts = pd.Timestamp(trade.exit_date)
            if exit_ts in realized.index:
                realized.loc[exit_ts] += trade.net_pnl_krw
            else:
                realized.loc[exit_ts] = trade.net_pnl_krw

        realized = realized.sort_index()
        equity_curve = initial_equity + realized.cumsum()
        return equity_curve

    def build_orders(self) -> list[Order]:
        """Build filled buy/sell orders for report rendering."""
        orders: list[Order] = []
        for idx, trade in enumerate(self.trades, 1):
            entry_dt = datetime.combine(trade.entry_date, datetime.min.time()).replace(hour=trade.entry_hour)
            exit_dt = datetime.combine(trade.exit_date, datetime.min.time()).replace(hour=trade.exit_hour)
            orders.append(
                Order(
                    id=f"{self.STRATEGY_ID}-buy-{idx}",
                    symbol=trade.ticker,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=trade.shares,
                    price=trade.entry_price,
                    filled_quantity=trade.shares,
                    average_price=trade.entry_price,
                    status=OrderStatus.FILLED,
                    created_at=entry_dt,
                    updated_at=entry_dt,
                    commission=trade.position_size * (self.params.cost_pct / 100.0) / 2.0,
                )
            )
            orders.append(
                Order(
                    id=f"{self.STRATEGY_ID}-sell-{idx}",
                    symbol=trade.ticker,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=trade.shares,
                    price=trade.exit_price,
                    filled_quantity=trade.shares,
                    average_price=trade.exit_price,
                    status=OrderStatus.FILLED,
                    created_at=exit_dt,
                    updated_at=exit_dt,
                    pnl=trade.net_pnl_krw,
                    commission=trade.position_size * (self.params.cost_pct / 100.0) / 2.0,
                )
            )
        return orders

    def to_backtest_result(
        self,
        start_date: date | datetime | str,
        end_date: date | datetime | str,
        initial_equity: Optional[float] = None,
    ) -> BacktestResult:
        """Convert the completed simulation to BacktestResult."""
        initial_cash = initial_equity if initial_equity is not None else self.initial_equity
        if initial_cash <= 0:
            raise ValueError("initial_equity must be positive")

        trades_df = self.get_results_df()
        equity_curve = self.build_equity_curve(initial_cash, start_date, end_date)
        daily_returns = equity_curve.pct_change().dropna()
        running_max = equity_curve.cummax()
        drawdown_series = equity_curve / running_max - 1.0
        max_drawdown = abs(float(drawdown_series.min())) if not drawdown_series.empty else 0.0

        total_return = float(equity_curve.iloc[-1] - initial_cash)
        total_return_pct = float(equity_curve.iloc[-1] / initial_cash - 1.0)

        start_dt = pd.Timestamp(_as_date(start_date))
        end_dt = pd.Timestamp(_as_date(end_date))
        period_days = max((end_dt - start_dt).days, 1)
        if equity_curve.iloc[-1] > 0 and initial_cash > 0:
            cagr = float((equity_curve.iloc[-1] / initial_cash) ** (365.0 / period_days) - 1.0)
        else:
            cagr = 0.0

        if not daily_returns.empty and daily_returns.std(ddof=0) > 0:
            sharpe_ratio = float((daily_returns.mean() / daily_returns.std(ddof=0)) * np.sqrt(252))
        else:
            sharpe_ratio = 0.0

        downside = daily_returns[daily_returns < 0]
        if not daily_returns.empty and not downside.empty and downside.std(ddof=0) > 0:
            sortino_ratio = float((daily_returns.mean() / downside.std(ddof=0)) * np.sqrt(252))
        else:
            sortino_ratio = 0.0

        if trades_df.empty:
            gross_profit = 0.0
            gross_loss = 0.0
            average_win = 0.0
            average_loss = 0.0
            win_rate = 0.0
            profit_factor = 0.0
        else:
            wins = trades_df[trades_df["net_pnl_krw"] > 0]
            losses = trades_df[trades_df["net_pnl_krw"] <= 0]
            gross_profit = float(wins["net_pnl_krw"].sum())
            gross_loss = float(abs(losses["net_pnl_krw"].sum()))
            average_win = float(wins["net_pnl_pct"].mean()) if not wins.empty else 0.0
            average_loss = float(losses["net_pnl_pct"].mean()) if not losses.empty else 0.0
            win_rate = float(len(wins) / len(trades_df))
            profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

        total_fees = float(trades_df["position_size"].sum() * (self.params.cost_pct / 100.0)) if not trades_df.empty else 0.0
        turnover = float((trades_df["position_size"].sum() * 2.0 / initial_cash) * 100.0) if not trades_df.empty else 0.0
        annual_std = float(daily_returns.std(ddof=0) * np.sqrt(252)) if not daily_returns.empty else 0.0

        raw_statistics = {
            "Strategy Name": self.STRATEGY_NAME,
            "Net Profit": total_return_pct * 100.0,
            "Compounding Annual Return": cagr * 100.0,
            "Drawdown": max_drawdown * 100.0,
            "Sharpe Ratio": sharpe_ratio,
            "Sortino Ratio": sortino_ratio,
            "Average Win": average_win,
            "Average Loss": average_loss,
            "Win Rate": win_rate * 100.0,
            "Total Fees": total_fees,
            "Portfolio Turnover": turnover,
            "Annual Standard Deviation": annual_std,
            "Annual Variance": annual_std ** 2,
            "Probabilistic Sharpe Ratio": 0.0,
            "Alpha": 0.0,
            "Beta": 0.0,
            "Information Ratio": 0.0,
            "Tracking Error": 0.0,
            "Treynor Ratio": 0.0,
            "Drawdown Recovery": 0.0,
            "Trade Count": int(len(trades_df)),
            "Params": asdict(self.params),
        }
        if self.last_artifacts:
            raw_statistics["Lean Artifacts"] = self.last_artifacts

        trade_summaries = [
            {
                "entry_date": trade.entry_date.isoformat(),
                "exit_date": trade.exit_date.isoformat(),
                "symbol": trade.ticker,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "entry_hour": trade.entry_hour,
                "exit_hour": trade.exit_hour,
                "exit_reason": trade.exit_reason,
                "quantity": trade.shares,
                "net_pnl_krw": trade.net_pnl_krw,
                "net_pnl_pct": trade.net_pnl_pct,
            }
            for trade in self.trades
        ]

        unique_symbols = sorted({trade.ticker for trade in self.trades})
        return BacktestResult(
            success=True,
            run_id=f"{self.STRATEGY_ID}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            strategy_id=self.STRATEGY_ID,
            symbols=unique_symbols,
            start_date=str(_as_date(start_date)),
            end_date=str(_as_date(end_date)),
            total_return=total_return,
            total_return_pct=total_return_pct,
            cagr=cagr,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown=max_drawdown,
            total_trades=len(self.trades),
            win_rate=win_rate,
            profit_factor=profit_factor,
            average_win=average_win,
            average_loss=average_loss,
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            orders=self.build_orders(),
            trades=trade_summaries,
            raw_statistics=raw_statistics,
            duration_seconds=self.last_run_seconds,
        )

    def export_supporting_artifacts(self, workspace_dir: str | Path) -> dict[str, str]:
        """Export hourly CSVs and ranking JSON for future Lean integration."""
        if not self.raw_data:
            raise ValueError("No data loaded to export")

        workspace = Path(workspace_dir)
        hourly_dir = workspace / "data" / "equity" / "krx" / "hourly"
        ranking_dir = workspace / "data" / "custom" / "krx"
        hourly_dir.mkdir(parents=True, exist_ok=True)
        ranking_dir.mkdir(parents=True, exist_ok=True)

        exported_symbols: list[str] = []
        exported_files: list[str] = []
        active_symbols = sorted(self.stock_days.keys() or self.raw_data.keys())
        for ticker in active_symbols:
            df = self.raw_data[ticker].copy()
            out = pd.DataFrame(
                {
                    "timestamp": pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": df["open"].astype(float),
                    "high": df["high"].astype(float),
                    "low": df["low"].astype(float),
                    "close": df["close"].astype(float),
                    "volume": df["volume"].astype(int),
                }
            )
            csv_path = hourly_dir / f"{ticker.lower()}.csv"
            out.to_csv(csv_path, index=False)
            exported_symbols.append(ticker)
            exported_files.append(str(csv_path))

        rankings_path = ranking_dir / f"{self.STRATEGY_ID}_rankings.json"
        rankings_payload = {
            str(current_date): [ticker for ticker, _ in ranked[: self.params.top_n_stocks]]
            for current_date, ranked in self.daily_ranked.items()
        }
        rankings_path.write_text(json.dumps(rankings_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        summary_path = ranking_dir / f"{self.STRATEGY_ID}_summary.json"
        summary_payload = {
            "strategy_id": self.STRATEGY_ID,
            "exported_symbols": exported_symbols,
            "params": asdict(self.params),
            "trade_count": len(self.trades),
            "data_dir": str(self.data_dir),
        }
        summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        artifacts = {
            "hourly_dir": str(hourly_dir),
            "rankings_path": str(rankings_path),
            "summary_path": str(summary_path),
            "symbol_count": str(len(exported_symbols)),
        }
        self.last_artifacts = artifacts
        return artifacts
