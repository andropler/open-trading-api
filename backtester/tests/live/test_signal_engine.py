from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from kis_backtest.live.signal.engine import SignalEngine, compose_signals
from kis_backtest.live.signal.models import (
    BASE_CONFIG,
    ExitProfile,
    Signal,
)


def _profile() -> ExitProfile:
    return ExitProfile(
        stop_loss_pct=3.0,
        take_profit_pct=10.0,
        trail_activation_pct=5.0,
        trail_pct=4.0,
        max_hold_days=1,
    )


def _sig(variant: str, hhmm: int) -> Signal:
    hour, minute = divmod(hhmm, 100)
    ts = pd.Timestamp(f"2026-05-05 {hour:02d}:{minute:02d}:00")
    return Signal(
        source=variant.split("_")[0],
        variant=variant,
        ticker="005930",
        entry_ts=ts,
        entry_price=70000.0,
        stop_price=67900.0,
        profile=_profile(),
        priority=5.0,
    )


@dataclass
class MockEngine:
    name: str
    signals: list[Signal] = field(default_factory=list)

    def candidate_signals(self, asof_date: pd.Timestamp) -> list[Signal]:
        return list(self.signals)


def _is_signal_engine(obj: object) -> bool:
    """Protocol 만족 여부 (duck typing)."""
    return (
        hasattr(obj, "name")
        and hasattr(obj, "candidate_signals")
        and callable(obj.candidate_signals)
    )


class TestProtocolConformance:
    def test_mock_satisfies_protocol(self):
        engine = MockEngine(name="mock")
        assert _is_signal_engine(engine)
        # SignalEngine Protocol 타입 변수에 할당 가능
        eng: SignalEngine = engine  # noqa: F841


class TestComposeSignals:
    def test_single_engine_passthrough(self):
        engine = MockEngine(
            name="reclaim",
            signals=[_sig("reclaim_strict", 935)],
        )
        asof = pd.Timestamp("2026-05-05")
        result = compose_signals([engine], asof, BASE_CONFIG)
        assert len(result) == 1
        assert result[0].variant == "reclaim_strict"

    def test_multiple_engines_merged(self):
        e1 = MockEngine(name="reclaim", signals=[_sig("reclaim_strict", 945)])
        e2 = MockEngine(name="orb", signals=[_sig("orb_event_quality", 1015)])
        e3 = MockEngine(name="native", signals=[_sig("native_close_top15", 1025)])
        asof = pd.Timestamp("2026-05-05")
        result = compose_signals([e1, e2, e3], asof, BASE_CONFIG)
        assert len(result) == 3
        variants = {s.variant for s in result}
        assert variants == {"reclaim_strict", "orb_event_quality", "native_close_top15"}

    def test_filters_applied_after_merge(self):
        # 각 엔진이 BASE_CONFIG 위반 신호 포함 → compose 가 필터링
        e1 = MockEngine(
            name="reclaim",
            signals=[
                _sig("reclaim_strict", 935),  # OK
                _sig("reclaim_strict", 950),  # 컷
            ],
        )
        e2 = MockEngine(
            name="orb",
            signals=[
                _sig("orb_event_quality", 1015),  # OK
                _sig("orb_event_quality", 1030),  # 컷 (range)
            ],
        )
        asof = pd.Timestamp("2026-05-05")
        result = compose_signals([e1, e2], asof, BASE_CONFIG)
        assert len(result) == 2

    def test_empty_engines_returns_empty(self):
        asof = pd.Timestamp("2026-05-05")
        assert compose_signals([], asof, BASE_CONFIG) == []

    def test_engines_returning_empty(self):
        e1 = MockEngine(name="reclaim", signals=[])
        e2 = MockEngine(name="orb", signals=[])
        asof = pd.Timestamp("2026-05-05")
        assert compose_signals([e1, e2], asof, BASE_CONFIG) == []
