from __future__ import annotations

import pandas as pd
import pytest

from kis_backtest.live.signal.models import (
    BASE_CONFIG,
    CompositeConfig,
    ExitProfile,
    Signal,
)


@pytest.fixture
def profile() -> ExitProfile:
    return ExitProfile(
        stop_loss_pct=3.0,
        take_profit_pct=10.0,
        trail_activation_pct=5.0,
        trail_pct=4.0,
        max_hold_days=1,
    )


class TestExitProfile:
    def test_defaults(self, profile):
        assert profile.exit_hhmm == 1430
        assert profile.weakness_exit_enabled is True
        assert profile.weakness_exit_hhmm == 1430


class TestSignalProperties:
    def test_entry_hhmm_and_date(self, profile):
        sig = Signal(
            source="reclaim",
            variant="reclaim_strict",
            ticker="005930",
            entry_ts=pd.Timestamp("2026-05-05 09:35:00"),
            entry_price=70000.0,
            stop_price=67900.0,
            profile=profile,
            priority=5.0,
        )
        assert sig.entry_hhmm == 935
        assert sig.date == pd.Timestamp("2026-05-05").date()

    def test_entry_hhmm_zero_padded(self, profile):
        sig = Signal(
            source="reclaim",
            variant="reclaim_strict",
            ticker="005930",
            entry_ts=pd.Timestamp("2026-05-05 14:05:00"),
            entry_price=70000.0,
            stop_price=67900.0,
            profile=profile,
            priority=5.0,
        )
        assert sig.entry_hhmm == 1405


class TestFromRaw:
    def test_basic_normalization(self, profile):
        sig = Signal.from_raw(
            source="reclaim",
            variant="reclaim_strict",
            ticker="005930",
            entry_ts="2026-05-05 09:35:00",
            entry_price=70000.0,
            profile=profile,
            priority=5.0,
        )
        assert sig is not None
        # computed_stop = 70000 * (1 - 0.03) = 67900
        assert sig.stop_price == pytest.approx(67900.0)
        assert sig.entry_hhmm == 935

    def test_explicit_stop_takes_max(self, profile):
        # explicit_stop 68500 > computed 67900 → 더 가까운(높은) 68500 채택
        sig = Signal.from_raw(
            source="reclaim",
            variant="reclaim_strict",
            ticker="005930",
            entry_ts="2026-05-05 09:35:00",
            entry_price=70000.0,
            profile=profile,
            priority=5.0,
            explicit_stop=68500.0,
        )
        assert sig is not None
        assert sig.stop_price == pytest.approx(68500.0)

    def test_explicit_stop_below_computed_uses_computed(self, profile):
        sig = Signal.from_raw(
            source="reclaim",
            variant="reclaim_strict",
            ticker="005930",
            entry_ts="2026-05-05 09:35:00",
            entry_price=70000.0,
            profile=profile,
            priority=5.0,
            explicit_stop=60000.0,
        )
        assert sig is not None
        assert sig.stop_price == pytest.approx(67900.0)

    def test_zero_price_returns_none(self, profile):
        sig = Signal.from_raw(
            source="reclaim",
            variant="reclaim_strict",
            ticker="005930",
            entry_ts="2026-05-05",
            entry_price=0.0,
            profile=profile,
            priority=5.0,
        )
        assert sig is None

    def test_negative_price_returns_none(self, profile):
        sig = Signal.from_raw(
            source="reclaim",
            variant="reclaim_strict",
            ticker="005930",
            entry_ts="2026-05-05",
            entry_price=-100.0,
            profile=profile,
            priority=5.0,
        )
        assert sig is None

    def test_nan_price_returns_none(self, profile):
        sig = Signal.from_raw(
            source="reclaim",
            variant="reclaim_strict",
            ticker="005930",
            entry_ts="2026-05-05",
            entry_price=float("nan"),
            profile=profile,
            priority=5.0,
        )
        assert sig is None

    def test_explicit_stop_above_entry_returns_none(self, profile):
        sig = Signal.from_raw(
            source="reclaim",
            variant="reclaim_strict",
            ticker="005930",
            entry_ts="2026-05-05",
            entry_price=70000.0,
            profile=profile,
            priority=5.0,
            explicit_stop=80000.0,
        )
        assert sig is None


class TestBaseConfig:
    def test_label(self):
        assert BASE_CONFIG.label == "pf_target_tighter_slots1"

    def test_three_variants(self):
        assert BASE_CONFIG.variants == frozenset(
            {"reclaim_strict", "orb_event_quality", "native_close_top15"}
        )

    def test_max_positions_one(self):
        assert BASE_CONFIG.max_positions == 1

    def test_orb_hhmm_range(self):
        assert BASE_CONFIG.hhmm_range_for("orb_event_quality") == (1000, 1020)

    def test_reclaim_allowed_hhmms(self):
        expected = frozenset({935, 940, 945, 955, 1000, 1005, 1010, 1015, 1025})
        assert BASE_CONFIG.allowed_set_for("reclaim_strict") == expected

    def test_native_allowed_hhmms(self):
        assert BASE_CONFIG.allowed_set_for("native_close_top15") == frozenset({1025})

    def test_unknown_variant_returns_none(self):
        assert BASE_CONFIG.hhmm_range_for("missing") is None
        assert BASE_CONFIG.allowed_set_for("missing") is None


class TestCompositeConfigImmutability:
    def test_frozen_cannot_mutate(self):
        cfg = CompositeConfig(
            label="x",
            variants=frozenset({"a"}),
            max_positions=1,
        )
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            cfg.label = "y"  # type: ignore[misc]

    def test_hashable_in_set(self):
        s = {BASE_CONFIG}
        assert BASE_CONFIG in s

    def test_hashable_as_dict_key(self):
        d = {BASE_CONFIG: "primary"}
        assert d[BASE_CONFIG] == "primary"
