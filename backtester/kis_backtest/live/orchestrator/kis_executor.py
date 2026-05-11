"""KISExecutorAdapter: KIS Brokerage submit_order → LiveOrderExecutor Protocol.

execute_step 의 LiveOrderExecutor Protocol 을 KIS providers/kis/brokerage 의
KISBrokerageProvider.submit_order 와 연결한다. enum 변환 + 반환값 추출만 담당.

Protocol 요구사항:
  submit_order(symbol, side, quantity, order_type='market', price=0) -> str(order_id)

실제 KIS provider 시그니처:
  submit_order(symbol, side: OrderSide, quantity, order_type: OrderType, price: float|None) -> Order
"""

from __future__ import annotations

from typing import Optional, Protocol

from kis_backtest.models import Order
from kis_backtest.models.enums import OrderSide, OrderType


class _BrokerageProvider(Protocol):
    """Structural Protocol — KISBrokerageProvider 또는 동일 시그니처 Fake 허용.

    KIS 실 provider 직접 import 를 회피해 단위 테스트가 자격증명 없이
    동작하도록 한다. 시그니처가 KISBrokerageProvider.submit_order 와 drift 되면
    런타임에서야 발견되므로 통합 테스트에서 둘을 한 번 wire-up 해 검증한다.
    """

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
    ) -> Order: ...


class KISExecutorAdapter:
    def __init__(self, provider: _BrokerageProvider):
        self._provider = provider

    def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str = "market",
        price: int = 0,
    ) -> str:
        # price 가 int 인 이유: execute_step 의 LiveOrderExecutor Protocol 시그니처
        # (price=0 KRW 정수)와 일치시키기 위함. 내부에서 float(price) 로 캐스팅해
        # KIS provider 의 Optional[float] 시그니처에 맞춘다.
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be buy/sell, got {side!r}")
        if order_type not in ("market", "limit"):
            raise ValueError(f"order_type must be market/limit, got {order_type!r}")
        if order_type == "limit" and price <= 0:
            raise ValueError(
                f"limit order requires positive price, got {price}"
            )
        side_enum = OrderSide.BUY if side == "buy" else OrderSide.SELL
        if order_type == "market":
            type_enum = OrderType.MARKET
            kis_price: Optional[float] = None
        else:
            type_enum = OrderType.LIMIT
            kis_price = float(price)
        order = self._provider.submit_order(
            symbol=symbol,
            side=side_enum,
            quantity=quantity,
            order_type=type_enum,
            price=kis_price,
        )
        return str(order.id)
