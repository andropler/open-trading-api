from __future__ import annotations

import json
import pandas as pd

from kis_backtest.custom import BreakoutV41Params, KRIntradayBreakoutV41Backtester


def _make_symbol_df(start_day: str, *, breakout: bool) -> pd.DataFrame:
    rows = []
    base_days = pd.to_datetime([start_day, "2026-01-06", "2026-01-07"])
    price = 100.0 if breakout else 80.0
    for current_day in base_days:
        for hour in [10, 11, 12, 13, 14]:
            ts = current_day.replace(hour=hour)
            open_price = price
            high_price = price + 1
            low_price = max(price - 1, 1)
            close_price = price + 0.5
            volume = 100

            if breakout and current_day.date().isoformat() == "2026-01-06" and hour == 10:
                open_price = 105
                high_price = 112
                low_price = 105
                close_price = 110
                volume = 400
            elif breakout and current_day.date().isoformat() == "2026-01-06" and hour == 11:
                open_price = 110
                high_price = 111
                low_price = 110
                close_price = 111
            elif breakout and current_day.date().isoformat() == "2026-01-07":
                open_price = 112
                high_price = 114
                low_price = 111
                close_price = 113

            rows.append(
                {
                    "timestamp": ts,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": volume,
                }
            )
            price = close_price
    return pd.DataFrame(rows)


def _build_runner() -> KRIntradayBreakoutV41Backtester:
    runner = KRIntradayBreakoutV41Backtester(params=BreakoutV41Params(min_price=0))
    runner.raw_data = {
        "AAA": _make_symbol_df("2026-01-05", breakout=True),
        "BBB": _make_symbol_df("2026-01-05", breakout=False),
    }
    runner.compute_rankings()
    runner.precompute()
    return runner


def test_breakout_runner_generates_expected_trade():
    runner = _build_runner()
    runner.run(
        initial_equity=10_000_000,
        max_positions=3,
        start_date="2026-01-06",
        end_date="2026-01-06",
    )

    assert len(runner.trades) == 1
    trade = runner.trades[0]
    assert trade.ticker == "AAA"
    assert trade.entry_hour == 11
    assert trade.exit_date.isoformat() == "2026-01-06"
    assert trade.exit_reason == "trailing_stop"
    assert trade.shares > 0


def test_breakout_runner_builds_backtest_result():
    runner = _build_runner()
    runner.run(
        initial_equity=10_000_000,
        max_positions=3,
        start_date="2026-01-06",
        end_date="2026-01-06",
    )
    result = runner.to_backtest_result(
        start_date="2026-01-06",
        end_date="2026-01-06",
        initial_equity=10_000_000,
    )

    assert result.strategy_id == "kr_intraday_breakout_v41"
    assert result.total_trades == 1
    assert len(result.orders) == 2
    assert len(result.trades) == 1
    assert result.total_return > 0
    assert result.equity_curve is not None


def test_breakout_runner_exports_supporting_artifacts(tmp_path):
    runner = _build_runner()
    runner.run(
        initial_equity=10_000_000,
        max_positions=3,
        start_date="2026-01-06",
        end_date="2026-01-06",
    )

    artifacts = runner.export_supporting_artifacts(tmp_path)

    rankings_path = tmp_path / "data" / "custom" / "krx" / "kr_intraday_breakout_v41_rankings.json"
    hourly_path = tmp_path / "data" / "equity" / "krx" / "hourly" / "aaa.csv"

    assert hourly_path.exists()
    assert rankings_path.exists()
    assert artifacts["hourly_dir"] == str(tmp_path / "data" / "equity" / "krx" / "hourly")

    payload = json.loads(rankings_path.read_text(encoding="utf-8"))
    assert "2026-01-06" in payload
    assert "AAA" in payload["2026-01-06"]
