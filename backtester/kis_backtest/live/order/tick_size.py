"""KOSPI/KOSDAQ 호가단위 라운딩과 주문 가격 검증.

2023년 KRX 호가가격단위 개편 기준. 가격 입력은 int/float/Decimal 허용,
출력은 항상 정수 KRW. 라운딩은 Decimal로 수행해 부동소수점 오차 차단.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Literal, Union

Market = Literal["KOSPI", "KOSDAQ"]
RoundMode = Literal["nearest", "down", "up"]
Price = Union[int, float, Decimal]

# (upper_bound_exclusive, tick_size). upper=None ⇒ 해당 구간 이상 모두.
_KOSPI_TICKS: list[tuple[int | None, int]] = [
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (None, 1_000),
]

_KOSDAQ_TICKS: list[tuple[int | None, int]] = [
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (None, 100),
]

_TABLES: dict[str, list[tuple[int | None, int]]] = {
    "KOSPI": _KOSPI_TICKS,
    "KOSDAQ": _KOSDAQ_TICKS,
}

DAILY_LIMIT_PCT = Decimal("0.30")


def _to_decimal(value: Price) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"price must be numeric, got bool: {value}")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value:  # NaN
            raise ValueError("price is NaN")
        return Decimal(str(value))
    raise ValueError(f"price must be numeric, got {type(value).__name__}")


def _check_market(market: str) -> None:
    if market not in _TABLES:
        raise ValueError(f"market must be KOSPI or KOSDAQ, got {market!r}")


def tick_size(price: Price, market: Market) -> int:
    p = _to_decimal(price)
    if p <= 0:
        raise ValueError(f"price must be positive, got {price}")
    _check_market(market)
    for upper, size in _TABLES[market]:
        if upper is None or p < upper:
            return size
    raise RuntimeError("unreachable: tick table missing open-ended bucket")


def round_to_tick(price: Price, market: Market, mode: RoundMode = "nearest") -> int:
    p = _to_decimal(price)
    if p <= 0:
        raise ValueError(f"price must be positive, got {price}")
    _check_market(market)
    size = Decimal(tick_size(p, market))
    quotient = p / size
    if mode == "nearest":
        quantized = quotient.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    elif mode == "down":
        quantized = quotient.quantize(Decimal("1"), rounding=ROUND_FLOOR)
    elif mode == "up":
        quantized = quotient.quantize(Decimal("1"), rounding=ROUND_CEILING)
    else:
        raise ValueError(f"mode must be one of nearest/down/up, got {mode!r}")
    return int(quantized * size)


def validate_limit_price(
    price: Price,
    side: str,
    base_price: Price,
    market: Market,
) -> None:
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be buy/sell, got {side!r}")
    p = _to_decimal(price)
    bp = _to_decimal(base_price)
    if p <= 0 or bp <= 0:
        raise ValueError(f"price and base_price must be positive (price={price}, base={base_price})")
    _check_market(market)
    if p != p.to_integral_value():
        raise ValueError(f"price must be integer KRW, got {price}")
    upper = round_to_tick(bp * (Decimal(1) + DAILY_LIMIT_PCT), market, mode="down")
    lower = round_to_tick(bp * (Decimal(1) - DAILY_LIMIT_PCT), market, mode="up")
    p_int = int(p)
    if p_int > upper:
        raise ValueError(
            f"price {p_int} exceeds daily upper limit {upper} (base={int(bp)}, market={market})"
        )
    if p_int < lower:
        raise ValueError(
            f"price {p_int} below daily lower limit {lower} (base={int(bp)}, market={market})"
        )
    expected = round_to_tick(p, market)
    if p_int != expected:
        raise ValueError(
            f"price {p_int} not aligned to tick (expected {expected}, market={market})"
        )


def round_qty(qty: Union[int, float]) -> int:
    if isinstance(qty, bool):
        raise ValueError(f"qty must be numeric, got bool: {qty}")
    if not isinstance(qty, (int, float)):
        raise ValueError(f"qty must be int-convertible, got {type(qty).__name__}")
    n = int(qty)
    if n < 1:
        raise ValueError(f"qty must be >= 1, got {n}")
    return n
