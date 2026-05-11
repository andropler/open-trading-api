from __future__ import annotations

from datetime import datetime
from typing import Optional

import pytest

from kis_backtest.live.orchestrator.kis_executor import KISExecutorAdapter
from kis_backtest.models import Order
from kis_backtest.models.enums import OrderSide, OrderStatus, OrderType


def _make_order(order_id: str, side: OrderSide, order_type: OrderType, price: float) -> Order:
    return Order(
        id=order_id,
        symbol="005930",
        side=side,
        order_type=order_type,
        quantity=10,
        price=price if order_type == OrderType.LIMIT else None,
        filled_quantity=0,
        average_price=0.0,
        status=OrderStatus.PENDING,
        created_at=datetime(2026, 5, 6, 9, 35),
        updated_at=datetime(2026, 5, 6, 9, 35),
    )


class FakeProvider:
    def __init__(
        self, *, raise_exc: Exception | None = None, order_id: str = "ORD-1"
    ):
        self.raise_exc = raise_exc
        self.order_id = order_id
        self.calls: list[tuple] = []

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
    ) -> Order:
        self.calls.append((symbol, side, quantity, order_type, price))
        if self.raise_exc is not None:
            raise self.raise_exc
        return _make_order(
            self.order_id, side, order_type, price if price is not None else 0.0
        )


class TestBuyMarket:
    def test_basic_buy_market(self):
        provider = FakeProvider(order_id="ORD-100")
        adapter = KISExecutorAdapter(provider)
        order_id = adapter.submit_order("005930", "buy", 10)
        assert order_id == "ORD-100"
        symbol, side, qty, order_type, price = provider.calls[0]
        assert symbol == "005930"
        assert side == OrderSide.BUY
        assert qty == 10
        assert order_type == OrderType.MARKET
        assert price is None  # market 은 price None 으로 전달


class TestSellLimit:
    def test_sell_limit_with_price(self):
        provider = FakeProvider(order_id="ORD-200")
        adapter = KISExecutorAdapter(provider)
        order_id = adapter.submit_order(
            "005930", "sell", 5, order_type="limit", price=72000
        )
        assert order_id == "ORD-200"
        symbol, side, qty, otype, price = provider.calls[0]
        assert side == OrderSide.SELL
        assert otype == OrderType.LIMIT
        assert price == 72000.0
        assert isinstance(price, float)


class TestValidation:
    def test_invalid_side(self):
        adapter = KISExecutorAdapter(FakeProvider())
        with pytest.raises(ValueError, match="side"):
            adapter.submit_order("005930", "long", 10)  # type: ignore[arg-type]

    def test_invalid_order_type(self):
        adapter = KISExecutorAdapter(FakeProvider())
        with pytest.raises(ValueError, match="order_type"):
            adapter.submit_order("005930", "buy", 10, order_type="stop")

    def test_limit_zero_price(self):
        adapter = KISExecutorAdapter(FakeProvider())
        with pytest.raises(ValueError, match="positive price"):
            adapter.submit_order("005930", "buy", 10, order_type="limit", price=0)

    def test_limit_negative_price(self):
        adapter = KISExecutorAdapter(FakeProvider())
        with pytest.raises(ValueError, match="positive price"):
            adapter.submit_order("005930", "buy", 10, order_type="limit", price=-100)


class TestProviderError:
    def test_provider_exception_propagates(self):
        provider = FakeProvider(raise_exc=RuntimeError("KIS 5xx"))
        adapter = KISExecutorAdapter(provider)
        with pytest.raises(RuntimeError, match="KIS 5xx"):
            adapter.submit_order("005930", "buy", 10)


class TestProtocolCompatibility:
    def test_satisfies_live_order_executor(self):
        # LiveOrderExecutor Protocol 만족 여부 — duck typing
        from kis_backtest.live.orchestrator.execute_step import LiveOrderExecutor

        adapter = KISExecutorAdapter(FakeProvider())
        executor: LiveOrderExecutor = adapter  # noqa: F841 — Protocol 호환 검증
        # 호출 가능성 확인
        order_id = adapter.submit_order("005930", "buy", 10)
        assert isinstance(order_id, str)


class TestIntegrationWithExecuteStep:
    def test_executor_used_by_execute_step(self, tmp_path):
        from datetime import date

        import pandas as pd

        from kis_backtest.live.orchestrator.execute_step import execute_step
        from kis_backtest.live.orchestrator.trade_step import DryRunTradeStepResult
        from kis_backtest.live.risk.killswitch import Killswitch
        from kis_backtest.live.signal.models import ExitProfile, Signal

        provider = FakeProvider(order_id="ORD-LIVE-1")
        adapter = KISExecutorAdapter(provider)
        killswitch = Killswitch(
            halt_flag_path=tmp_path / "HALT.flag",
            archive_dir=tmp_path / "halts",
            capital_krw=5_000_000,
        )
        sig = Signal(
            source="reclaim",
            variant="reclaim_strict",
            ticker="005930",
            entry_ts=pd.Timestamp("2026-05-06 09:35:00"),
            entry_price=70000.0,
            stop_price=67900.0,
            profile=ExitProfile(
                stop_loss_pct=3.0,
                take_profit_pct=10.0,
                trail_activation_pct=5.0,
                trail_pct=4.0,
                max_hold_days=1,
            ),
            priority=5.0,
        )
        trade = DryRunTradeStepResult(
            asof_date=date(2026, 5, 6),
            entries_allowed=True,
            candidates_count=1,
            selected_count=1,
            selected=(sig,),
        )
        results = execute_step(
            trade, adapter, killswitch, 5_000_000, telegram=None, dry_run=False
        )
        assert len(results) == 1
        assert results[0].submitted
        assert results[0].order_id == "ORD-LIVE-1"
        assert provider.calls[0][1] == OrderSide.BUY
