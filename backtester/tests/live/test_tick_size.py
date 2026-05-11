from __future__ import annotations

import pytest

from kis_backtest.live.order.tick_size import (
    round_qty,
    round_to_tick,
    tick_size,
    validate_limit_price,
)


class TestKospiTicks:
    def test_under_2k_uses_1_won(self):
        assert tick_size(1500, "KOSPI") == 1
        assert round_to_tick(1234, "KOSPI") == 1234

    def test_2k_to_5k_uses_5_won(self):
        assert tick_size(3000, "KOSPI") == 5
        assert round_to_tick(3001, "KOSPI") == 3000
        assert round_to_tick(3003, "KOSPI") == 3005

    def test_5k_to_20k_uses_10_won(self):
        assert tick_size(12345, "KOSPI") == 10
        assert round_to_tick(12345, "KOSPI") == 12350

    def test_20k_to_50k_uses_50_won(self):
        assert tick_size(33333, "KOSPI") == 50
        assert round_to_tick(33333, "KOSPI") == 33350

    def test_50k_to_200k_uses_100_won(self):
        assert tick_size(123456, "KOSPI") == 100
        assert round_to_tick(123456, "KOSPI") == 123500

    def test_200k_to_500k_uses_500_won(self):
        assert tick_size(300000, "KOSPI") == 500
        assert round_to_tick(300251, "KOSPI") == 300500

    def test_above_500k_uses_1000_won(self):
        assert tick_size(800000, "KOSPI") == 1000
        assert round_to_tick(800499, "KOSPI") == 800000
        assert round_to_tick(800500, "KOSPI") == 801000


class TestKosdaqTicks:
    def test_kosdaq_under_50k_matches_kospi(self):
        assert tick_size(1500, "KOSDAQ") == 1
        assert tick_size(33000, "KOSDAQ") == 50

    def test_kosdaq_above_50k_uses_100_won(self):
        assert tick_size(123456, "KOSDAQ") == 100
        assert tick_size(800000, "KOSDAQ") == 100
        assert round_to_tick(800499, "KOSDAQ") == 800500


class TestRoundingModes:
    def test_mode_down(self):
        assert round_to_tick(12349, "KOSPI", mode="down") == 12340

    def test_mode_up(self):
        assert round_to_tick(12341, "KOSPI", mode="up") == 12350

    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="mode"):
            round_to_tick(1000, "KOSPI", mode="banker")  # type: ignore[arg-type]


class TestInvalidInputs:
    def test_negative_price(self):
        with pytest.raises(ValueError, match="positive"):
            round_to_tick(-100, "KOSPI")

    def test_zero_price(self):
        with pytest.raises(ValueError, match="positive"):
            round_to_tick(0, "KOSPI")

    def test_non_numeric_string(self):
        with pytest.raises(ValueError):
            round_to_tick("12345", "KOSPI")  # type: ignore[arg-type]

    def test_invalid_market(self):
        with pytest.raises(ValueError, match="KOSPI or KOSDAQ"):
            round_to_tick(1000, "NYSE")  # type: ignore[arg-type]

    def test_bool_rejected(self):
        with pytest.raises(ValueError, match="bool"):
            round_to_tick(True, "KOSPI")  # type: ignore[arg-type]


class TestValidateLimitPrice:
    def test_within_limits(self):
        validate_limit_price(10000, "buy", 10000, "KOSPI")
        validate_limit_price(13000, "buy", 10000, "KOSPI")
        validate_limit_price(7000, "sell", 10000, "KOSPI")

    def test_exceeds_upper(self):
        with pytest.raises(ValueError, match="upper limit"):
            validate_limit_price(13010, "buy", 10000, "KOSPI")

    def test_below_lower(self):
        with pytest.raises(ValueError, match="lower limit"):
            validate_limit_price(6990, "sell", 10000, "KOSPI")

    def test_unaligned_price(self):
        with pytest.raises(ValueError, match="not aligned"):
            validate_limit_price(12345, "buy", 12000, "KOSPI")

    def test_invalid_side(self):
        with pytest.raises(ValueError, match="side"):
            validate_limit_price(10000, "long", 10000, "KOSPI")  # type: ignore[arg-type]

    def test_non_integer_float_rejected(self):
        # 12340.5 → int(12340.5) == 12340, round_to_tick(12340.5) == 12340 둘 다 같지만
        # 비정수 float 가격은 silent truncation 위험이라 명시적 거부.
        with pytest.raises(ValueError, match="integer KRW"):
            validate_limit_price(12340.5, "buy", 12000, "KOSPI")


class TestRoundQty:
    def test_positive_int(self):
        assert round_qty(10) == 10

    def test_float_truncates(self):
        assert round_qty(10.7) == 10

    def test_zero_rejected(self):
        with pytest.raises(ValueError, match=">= 1"):
            round_qty(0)

    def test_negative_rejected(self):
        with pytest.raises(ValueError, match=">= 1"):
            round_qty(-5)

    def test_bool_rejected(self):
        with pytest.raises(ValueError, match="bool"):
            round_qty(True)  # type: ignore[arg-type]
