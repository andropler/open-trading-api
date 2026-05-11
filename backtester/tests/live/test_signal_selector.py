from __future__ import annotations

import pandas as pd
import pytest

from kis_backtest.live.signal.models import (
    BASE_CONFIG,
    CompositeConfig,
    ExitProfile,
    Signal,
)
from kis_backtest.live.signal.selector import select_signals


@pytest.fixture
def profile() -> ExitProfile:
    return ExitProfile(
        stop_loss_pct=3.0,
        take_profit_pct=10.0,
        trail_activation_pct=5.0,
        trail_pct=4.0,
        max_hold_days=1,
    )


def _sig(variant: str, hhmm: int, profile: ExitProfile, ticker: str = "005930") -> Signal:
    hour, minute = divmod(hhmm, 100)
    ts = pd.Timestamp(f"2026-05-05 {hour:02d}:{minute:02d}:00")
    return Signal(
        source="reclaim",
        variant=variant,
        ticker=ticker,
        entry_ts=ts,
        entry_price=70000.0,
        stop_price=67900.0,
        profile=profile,
        priority=5.0,
    )


class TestVariantFilter:
    def test_unknown_variant_dropped(self, profile):
        signals = [
            _sig("unknown_variant", 1000, profile),
            _sig("reclaim_strict", 935, profile),
        ]
        selected = select_signals(signals, BASE_CONFIG)
        assert len(selected) == 1
        assert selected[0].variant == "reclaim_strict"

    def test_all_unknown_returns_empty(self, profile):
        signals = [_sig("foo", 1000, profile), _sig("bar", 935, profile)]
        assert select_signals(signals, BASE_CONFIG) == []

    def test_empty_input(self, profile):
        assert select_signals([], BASE_CONFIG) == []


class TestHhmmRanges:
    def test_orb_within_range(self, profile):
        sig = _sig("orb_event_quality", 1010, profile)
        assert select_signals([sig], BASE_CONFIG) == [sig]

    def test_orb_outside_range_dropped(self, profile):
        sig = _sig("orb_event_quality", 1030, profile)
        assert select_signals([sig], BASE_CONFIG) == []

    def test_orb_at_boundary(self, profile):
        sig_low = _sig("orb_event_quality", 1000, profile)
        sig_high = _sig("orb_event_quality", 1020, profile)
        result = select_signals([sig_low, sig_high], BASE_CONFIG)
        assert len(result) == 2


class TestAllowedHhmms:
    def test_reclaim_at_allowed_minute(self, profile):
        sig = _sig("reclaim_strict", 945, profile)
        assert select_signals([sig], BASE_CONFIG) == [sig]

    def test_reclaim_at_disallowed_minute(self, profile):
        sig = _sig("reclaim_strict", 950, profile)
        assert select_signals([sig], BASE_CONFIG) == []

    def test_native_only_1025(self, profile):
        sig_pass = _sig("native_close_top15", 1025, profile)
        sig_fail = _sig("native_close_top15", 1030, profile)
        result = select_signals([sig_pass, sig_fail], BASE_CONFIG)
        assert result == [sig_pass]


class TestRangesAndAllowedTogether:
    def test_both_ranges_and_allowed_apply_and(self, profile):
        # variant이 hhmm_ranges + allowed_hhmms 양쪽에 정의된 경우 둘 다 통과해야 함
        cfg = CompositeConfig(
            label="dual",
            variants=frozenset({"reclaim_strict"}),
            max_positions=1,
            hhmm_ranges=(("reclaim_strict", 940, 1010),),
            allowed_hhmms=(("reclaim_strict", frozenset({940, 1015})),),
        )
        # 940: range OK + allowed OK → 통과
        # 1015: range NG (>1010) → 컷
        # 945: range OK + allowed NG → 컷
        s_940 = _sig("reclaim_strict", 940, profile)
        s_1015 = _sig("reclaim_strict", 1015, profile)
        s_945 = _sig("reclaim_strict", 945, profile)
        result = select_signals([s_940, s_1015, s_945], cfg)
        assert result == [s_940]


class TestMixedRealistic:
    def test_full_base_config_filter(self, profile):
        signals = [
            _sig("reclaim_strict", 935, profile),  # OK (allowed)
            _sig("reclaim_strict", 950, profile),  # 컷 (not allowed)
            _sig("orb_event_quality", 1015, profile),  # OK (range)
            _sig("orb_event_quality", 1030, profile),  # 컷 (out of range)
            _sig("native_close_top15", 1025, profile),  # OK
            _sig("native_close_top15", 1030, profile),  # 컷
            _sig("unknown_variant", 1000, profile),  # 컷 (variant 미일치)
        ]
        result = select_signals(signals, BASE_CONFIG)
        assert len(result) == 3
        variants = {s.variant for s in result}
        assert variants == {"reclaim_strict", "orb_event_quality", "native_close_top15"}
