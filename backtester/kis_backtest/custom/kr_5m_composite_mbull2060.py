"""KR 5m composite m_bull_20_60 custom backtester.

This adapter ports the validated alpha-hunter research flow into the
open-trading-api backtester workspace. It keeps the execution entry point in
`backtester/kis_backtest/custom` and writes artifacts under
`backtester/examples/output`, while reusing the previously validated 5m signal
builders and 1m execution simulator from the sibling alpha-hunter workspace.

The strategy uses:
    - 5m signal generation
    - 1m execution replay
    - max_positions = 1
    - market regime filter m_bull_20_60
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from ..models import BacktestResult, Order, OrderSide, OrderStatus, OrderType


INITIAL_EQUITY = 10_000_000
DEFAULT_ALPHA_ROOT = Path(__file__).resolve().parents[4] / "alpha-hunter"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "examples" / "output" / "kr_5m_composite_mbull2060"
RULE = "m_bull_20_60"
COSTS = [0.55, 0.75, 1.00, 1.25, 1.50, 2.00]


@dataclass
class CompositeMBull2060Params:
    """Fixed parameters for the final KR 5m composite strategy."""

    alpha_root: Path = DEFAULT_ALPHA_ROOT
    output_dir: Path = DEFAULT_OUTPUT_DIR
    initial_equity: float = INITIAL_EQUITY
    cost_pct: float = 0.55
    market_rule: str = RULE
    base_config_label: str = "pf_target_tighter_slots1"
    start_date: str = "2025-04-25"
    end_date: str = "2026-04-29"
    write_artifacts: bool = True


@dataclass
class CompositeMBull2060Result:
    """Backtest result summary plus artifact paths."""

    summary: dict[str, Any]
    cost_stress: list[dict[str, Any]]
    monthly: list[dict[str, Any]]
    source: list[dict[str, Any]]
    artifacts: dict[str, str] = field(default_factory=dict)


def _ensure_alpha_imports(alpha_root: Path) -> None:
    if not alpha_root.exists():
        raise FileNotFoundError(
            f"alpha-hunter workspace not found: {alpha_root}. "
            "Pass CompositeMBull2060Params(alpha_root=...) if it is in a different location."
        )
    alpha_str = str(alpha_root)
    if alpha_str not in sys.path:
        sys.path.insert(0, alpha_str)


def _pf(df: pd.DataFrame, cost_pct: float) -> float:
    if df.empty:
        return 0.0
    pnl = df["pnl_pct"].astype(float) - cost_pct
    gp = pnl[pnl > 0].sum()
    gl = abs(pnl[pnl <= 0].sum())
    return float(gp / gl) if gl > 0 else float("inf")


def _equity_stats(df: pd.DataFrame, final_equity: float, initial_equity: float) -> tuple[float, float, float]:
    if df.empty:
        return 0.0, 0.0, 0.0
    ordered = df.sort_values("exit_timestamp")
    curve = initial_equity + ordered["pnl_krw"].astype(float).cumsum()
    values = np.r_[initial_equity, curve.values]
    peak = np.maximum.accumulate(values)
    mdd = float(((values - peak) / peak).min() * 100)
    total_return = float((final_equity / initial_equity - 1) * 100)
    days = (pd.to_datetime(ordered["date"]).max() - pd.to_datetime(ordered["date"]).min()).days
    years = max(days / 365.25, 30 / 365.25)
    cagr = float(((final_equity / initial_equity) ** (1 / years) - 1) * 100)
    return cagr, total_return, mdd


def _top3(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    by_ticker = df.groupby("ticker")["pnl_krw"].sum().sort_values(ascending=False)
    gp = by_ticker[by_ticker > 0].sum()
    return float(by_ticker.head(3).clip(lower=0).sum() / gp * 100) if gp > 0 else 0.0


def _summary(
    label: str,
    max_positions: int,
    signal_count: int,
    df: pd.DataFrame,
    final_equity: float,
    missed_entries: int,
    initial_equity: float,
) -> dict[str, Any]:
    cagr, total_return, mdd = _equity_stats(df, final_equity, initial_equity)
    pnl = df["pnl_pct"].astype(float) - 0.55 if not df.empty else pd.Series(dtype=float)
    source_counts = df["source"].value_counts() if not df.empty else pd.Series(dtype=int)
    return {
        "label": label,
        "max_positions": max_positions,
        "signal_count": int(signal_count),
        "trades": int(len(df)),
        "missed_entries": int(missed_entries),
        "win_rate": float((pnl > 0).mean() * 100) if len(pnl) else 0.0,
        "pf_055": _pf(df, 0.55),
        "pf_075": _pf(df, 0.75),
        "pf_100": _pf(df, 1.00),
        "cagr": cagr,
        "total_return": total_return,
        "mdd": mdd,
        "top3_gross_profit_pct": _top3(df),
        "reclaim_trades": int(source_counts.get("reclaim", 0)),
        "orb_trades": int(source_counts.get("orb", 0)),
        "native_trades": int(source_counts.get("native", 0)),
        "final_equity": float(final_equity),
    }


def _cost_adjusted_pnl(df: pd.DataFrame, cost_pct: float) -> pd.Series:
    return df["pnl_krw"].astype(float) - df["position_size"].astype(float) * ((cost_pct - 0.55) / 100)


def _cost_stress(df: pd.DataFrame, initial_equity: float) -> list[dict[str, Any]]:
    rows = []
    for cost in COSTS:
        if df.empty:
            rows.append(
                {
                    "cost_pct": cost,
                    "pf": 0.0,
                    "total_return": 0.0,
                    "mdd": 0.0,
                    "win_rate": 0.0,
                    "positive_months": 0,
                    "negative_months": 0,
                }
            )
            continue
        net_pct = df["pnl_pct"].astype(float) - cost
        adj_pnl = _cost_adjusted_pnl(df, cost)
        curve = initial_equity + adj_pnl.cumsum()
        peak = curve.cummax()
        monthly = (
            df.assign(month=pd.to_datetime(df["entry_timestamp"]).dt.to_period("M").astype(str), adj_pnl=adj_pnl)
            .groupby("month")
            .agg(pnl_krw=("adj_pnl", "sum"))
        )
        rows.append(
            {
                "cost_pct": cost,
                "pf": _pf(pd.DataFrame({"pnl_pct": df["pnl_pct"]}), cost),
                "total_return": float(adj_pnl.sum() / initial_equity * 100),
                "mdd": float(((curve - peak) / peak).min() * 100) if len(curve) else 0.0,
                "win_rate": float((net_pct > 0).mean() * 100) if len(net_pct) else 0.0,
                "positive_months": int((monthly["pnl_krw"] > 0).sum()),
                "negative_months": int((monthly["pnl_krw"] <= 0).sum()),
            }
        )
    return rows


def _group_pf(df: pd.DataFrame, group_cols: list[str]) -> list[dict[str, Any]]:
    if df.empty:
        return []
    rows = []
    for key, grp in df.groupby(group_cols):
        if not isinstance(key, tuple):
            key = (key,)
        row = {col: value for col, value in zip(group_cols, key)}
        row.update(
            {
                "trades": int(len(grp)),
                "pnl_krw": float(grp["pnl_krw"].sum()),
                "pf_055": _pf(grp, 0.55),
                "pf_100": _pf(grp, 1.00),
            }
        )
        rows.append(row)
    return rows


def _monthly(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    tmp = df.assign(month=pd.to_datetime(df["entry_timestamp"]).dt.to_period("M").astype(str))
    rows = []
    for month, grp in tmp.groupby("month"):
        rows.append(
            {
                "month": month,
                "trades": int(len(grp)),
                "pnl_krw": float(grp["pnl_krw"].sum()),
                "pf_055": _pf(grp, 0.55),
                "pf_100": _pf(grp, 1.00),
            }
        )
    return rows


def _write_markdown(payload: dict[str, Any], path: Path) -> None:
    summary = payload["summary"]
    lines = [
        "# Final KR 5m Composite Strategy: m_bull_20_60",
        "",
        "## Fixed Rule",
        "",
        "- Base strategy: `pf_target_tighter_slots1`",
        "- Market regime: `m_bull_20_60`",
        "- Rule: previous day `069500` close > SMA20, SMA20 > SMA60, 5-day return > 0",
        "- Signal timeframe: 5m",
        "- Execution timeframe: 1m",
        "- Max positions: 1",
        "",
        "## Summary",
        "",
        f"- Signals: {summary['signal_count']}",
        f"- Trades: {summary['trades']}",
        f"- Total return: {summary['total_return']:.1f}%",
        f"- PF@0.55: {summary['pf_055']:.3f}",
        f"- PF@1.00: {summary['pf_100']:.3f}",
        f"- MDD: {summary['mdd']:.1f}%",
        f"- Source trades R/O/N: {summary['reclaim_trades']}/{summary['orb_trades']}/{summary['native_trades']}",
        "",
        "## Cost Stress",
        "",
        "| Cost% | PF | Total% | MDD% | Win% | +Months | -Months |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["cost_stress"]:
        lines.append(
            f"| {row['cost_pct']:.2f} | {row['pf']:.3f} | {row['total_return']:.1f} | {row['mdd']:.1f} | "
            f"{row['win_rate']:.1f} | {row['positive_months']} | {row['negative_months']} |"
        )
    lines.extend(["", "## Monthly", "", "| Month | Trades | PnL KRW | PF@0.55 | PF@1.00 |", "|---|---:|---:|---:|---:|"])
    for row in payload["monthly"]:
        lines.append(f"| {row['month']} | {row['trades']} | {row['pnl_krw']:.0f} | {row['pf_055']:.3f} | {row['pf_100']:.3f} |")
    lines.extend(["", "## Sources", "", "| Source | Variant | Trades | PnL KRW | PF@0.55 | PF@1.00 |", "|---|---|---:|---:|---:|---:|"])
    for row in payload["source"]:
        lines.append(
            f"| {row['source']} | {row['variant']} | {row['trades']} | {row['pnl_krw']:.0f} | "
            f"{row['pf_055']:.3f} | {row['pf_100']:.3f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class KR5mCompositeMBull2060Backtester:
    """Standalone open-trading-api entry point for the final KR 5m strategy."""

    STRATEGY_ID = "kr_5m_composite_mbull2060"
    STRATEGY_NAME = "KR 5m Composite m_bull_20_60"

    def __init__(self, params: Optional[CompositeMBull2060Params] = None) -> None:
        self.params = params or CompositeMBull2060Params()
        self.signals: list[dict[str, Any]] = []
        self.selected_signals: list[dict[str, Any]] = []
        self.raw_1m: dict[str, pd.DataFrame] = {}
        self.trades: pd.DataFrame = pd.DataFrame()
        self.final_equity: float = 0.0
        self.missed_entries: int = 0
        self.result: Optional[CompositeMBull2060Result] = None
        self.last_run_seconds: float = 0.0

    def run(self) -> CompositeMBull2060Result:
        start_time = time.perf_counter()
        _ensure_alpha_imports(self.params.alpha_root)

        from scripts.filter_kr_5m_composite_market_regime import BASE_CONFIG, _build_flags, _filter_by_rule
        from scripts.research_kr_5m_composite_strategy import _select_signals
        from scripts.validate_kr_5m_composite_1m_execution import _load_1m_data, _load_5m_signals, simulate_1m

        self.signals, start, end = _load_5m_signals()
        base_signals = _select_signals(self.signals, BASE_CONFIG)
        flags = _build_flags()
        self.selected_signals = _filter_by_rule(base_signals, flags, self.params.market_rule)
        tickers = {str(sig["ticker"]).zfill(6) for sig in self.selected_signals}
        self.raw_1m = _load_1m_data(tickers, start, end)

        self.trades, self.final_equity, self.missed_entries = simulate_1m(
            self.raw_1m,
            self.selected_signals,
            max_positions=BASE_CONFIG.max_positions,
            initial_equity=self.params.initial_equity,
            cost_pct=self.params.cost_pct,
        )

        summary = _summary(
            self.params.market_rule,
            BASE_CONFIG.max_positions,
            len(self.selected_signals),
            self.trades,
            self.final_equity,
            self.missed_entries,
            self.params.initial_equity,
        )
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "strategy_id": self.STRATEGY_ID,
            "strategy_name": self.STRATEGY_NAME,
            "date_range": {"start": start, "end": end},
            "base_config": self.params.base_config_label,
            "market_rule": self.params.market_rule,
            "initial_equity": self.params.initial_equity,
            "summary": summary,
            "cost_stress": _cost_stress(self.trades, self.params.initial_equity),
            "monthly": _monthly(self.trades),
            "source": _group_pf(self.trades, ["source", "variant"]),
        }
        artifacts: dict[str, str] = {}
        if self.params.write_artifacts:
            output_dir = Path(self.params.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            json_path = output_dir / "kr_5m_composite_mbull2060_final.json"
            md_path = output_dir / "kr_5m_composite_mbull2060_final.md"
            trades_path = output_dir / "kr_5m_composite_mbull2060_final_trades.csv"
            json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            _write_markdown(payload, md_path)
            self.trades.to_csv(trades_path, index=False)
            artifacts = {
                "json": str(json_path),
                "markdown": str(md_path),
                "trades_csv": str(trades_path),
            }

        self.last_run_seconds = time.perf_counter() - start_time
        self.result = CompositeMBull2060Result(
            summary=summary,
            cost_stress=payload["cost_stress"],
            monthly=payload["monthly"],
            source=payload["source"],
            artifacts=artifacts,
        )
        return self.result

    def result_dict(self) -> dict[str, Any]:
        if self.result is None:
            raise ValueError("run() must be called before result_dict()")
        return asdict(self.result)

    def build_equity_curve(self, initial_equity: Optional[float] = None) -> pd.Series:
        """Build a daily equity curve compatible with the HTML report generator."""
        initial_cash = float(initial_equity if initial_equity is not None else self.params.initial_equity)
        if self.trades.empty:
            return pd.Series([initial_cash], index=pd.DatetimeIndex([pd.Timestamp(self.params.start_date)]))

        ordered = self.trades.copy()
        ordered["exit_timestamp"] = pd.to_datetime(ordered["exit_timestamp"])
        ordered = ordered.sort_values("exit_timestamp")
        curve = initial_cash + ordered.groupby(ordered["exit_timestamp"].dt.normalize())["pnl_krw"].sum().cumsum()

        start = pd.Timestamp(self.params.start_date)
        end = pd.Timestamp(self.params.end_date)
        index = pd.date_range(start=start, end=end, freq="D")
        equity = pd.Series(np.nan, index=index, dtype=float)
        equity.iloc[0] = initial_cash
        equity.update(curve)
        equity = equity.ffill()
        return equity

    def build_orders(self) -> list[Order]:
        """Convert completed trades into buy/sell orders for the common report table."""
        if self.trades.empty:
            return []

        orders: list[Order] = []
        for idx, row in self.trades.reset_index(drop=True).iterrows():
            entry_dt = pd.Timestamp(row["entry_timestamp"]).to_pydatetime()
            exit_dt = pd.Timestamp(row["exit_timestamp"]).to_pydatetime()
            ticker = str(row["ticker"]).zfill(6)
            shares = int(row["shares"])
            position_size = float(row["position_size"])
            commission_half = position_size * (self.params.cost_pct / 100.0) / 2.0
            orders.append(
                Order(
                    id=f"{self.STRATEGY_ID}-buy-{idx}",
                    symbol=ticker,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=shares,
                    price=float(row["entry_price"]),
                    filled_quantity=shares,
                    average_price=float(row["entry_price"]),
                    status=OrderStatus.FILLED,
                    created_at=entry_dt,
                    updated_at=entry_dt,
                    commission=commission_half,
                )
            )
            orders.append(
                Order(
                    id=f"{self.STRATEGY_ID}-sell-{idx}",
                    symbol=ticker,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=shares,
                    price=float(row["exit_price"]),
                    filled_quantity=shares,
                    average_price=float(row["exit_price"]),
                    status=OrderStatus.FILLED,
                    created_at=exit_dt,
                    updated_at=exit_dt,
                    pnl=float(row["pnl_krw"]),
                    commission=commission_half,
                )
            )
        return orders

    def to_backtest_result(self) -> BacktestResult:
        """Convert the completed custom simulation to open-trading-api BacktestResult."""
        if self.result is None:
            raise ValueError("run() must be called before to_backtest_result()")

        initial_cash = float(self.params.initial_equity)
        equity_curve = self.build_equity_curve(initial_cash)
        daily_returns = equity_curve.pct_change().dropna()
        running_max = equity_curve.cummax()
        drawdown = equity_curve / running_max - 1.0
        max_drawdown = abs(float(drawdown.min())) if not drawdown.empty else 0.0

        total_return = float(equity_curve.iloc[-1] - initial_cash)
        total_return_pct = float(equity_curve.iloc[-1] / initial_cash - 1.0)
        period_days = max((pd.Timestamp(self.params.end_date) - pd.Timestamp(self.params.start_date)).days, 1)
        cagr = float((equity_curve.iloc[-1] / initial_cash) ** (365.0 / period_days) - 1.0) if equity_curve.iloc[-1] > 0 else 0.0

        if not daily_returns.empty and daily_returns.std(ddof=0) > 0:
            sharpe_ratio = float((daily_returns.mean() / daily_returns.std(ddof=0)) * np.sqrt(252))
        else:
            sharpe_ratio = 0.0

        downside = daily_returns[daily_returns < 0]
        if not daily_returns.empty and not downside.empty and downside.std(ddof=0) > 0:
            sortino_ratio = float((daily_returns.mean() / downside.std(ddof=0)) * np.sqrt(252))
        else:
            sortino_ratio = 0.0

        if self.trades.empty:
            win_rate = 0.0
            profit_factor = 0.0
            average_win = 0.0
            average_loss = 0.0
            total_fees = 0.0
            unique_symbols: list[str] = []
            trade_summaries: list[dict[str, Any]] = []
        else:
            pnl_krw = self.trades["pnl_krw"].astype(float)
            wins = self.trades[pnl_krw > 0]
            losses = self.trades[pnl_krw <= 0]
            gross_profit = float(wins["pnl_krw"].sum())
            gross_loss = float(abs(losses["pnl_krw"].sum()))
            win_rate = float(len(wins) / len(self.trades))
            profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
            average_win = float(wins["pnl_pct"].mean()) if not wins.empty else 0.0
            average_loss = float(losses["pnl_pct"].mean()) if not losses.empty else 0.0
            total_fees = float(self.trades["position_size"].sum() * (self.params.cost_pct / 100.0))
            unique_symbols = sorted(str(ticker).zfill(6) for ticker in self.trades["ticker"].unique())
            trade_summaries = [
                {
                    "date": str(row["date"]),
                    "symbol": str(row["ticker"]).zfill(6),
                    "source": row["source"],
                    "variant": row["variant"],
                    "entry_timestamp": str(row["entry_timestamp"]),
                    "exit_timestamp": str(row["exit_timestamp"]),
                    "entry_price": float(row["entry_price"]),
                    "exit_price": float(row["exit_price"]),
                    "quantity": int(row["shares"]),
                    "net_pnl_krw": float(row["pnl_krw"]),
                    "net_pnl_pct": float(row["pnl_pct"] - self.params.cost_pct),
                    "exit_reason": row["exit_reason"],
                }
                for _, row in self.trades.iterrows()
            ]

        turnover = float((self.trades["position_size"].sum() * 2.0 / initial_cash) * 100.0) if not self.trades.empty else 0.0
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
            "Profit Factor @ Cost": profit_factor,
            "Signal Count": len(self.selected_signals),
            "Missed Entries": self.missed_entries,
            "Reclaim Trades": self.result.summary.get("reclaim_trades", 0),
            "ORB Trades": self.result.summary.get("orb_trades", 0),
            "Native Trades": self.result.summary.get("native_trades", 0),
            "Params": {
                "market_rule": self.params.market_rule,
                "base_config": self.params.base_config_label,
                "signal_timeframe": "5m",
                "execution_timeframe": "1m",
                "cost_pct": self.params.cost_pct,
            },
        }

        return BacktestResult(
            success=True,
            run_id=f"{self.STRATEGY_ID}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            strategy_id=self.STRATEGY_ID,
            symbols=unique_symbols,
            start_date=self.params.start_date,
            end_date=self.params.end_date,
            total_return=total_return,
            total_return_pct=total_return_pct,
            cagr=cagr,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown=max_drawdown,
            total_trades=int(len(self.trades)),
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
