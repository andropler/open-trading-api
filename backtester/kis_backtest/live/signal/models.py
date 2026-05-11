"""신호 도메인 모델 (Signal, ExitProfile, CompositeConfig) + BASE_CONFIG 상수.

alpha-hunter scripts/research_kr_5m_composite_strategy.py:38-77 의 ExitProfile/
CompositeConfig 와 동치. _normalize_signal(line 129-180) 의 입력→출력 변환을
Signal.from_raw 클래스메서드로 캡슐화. BASE_CONFIG 는 alpha-hunter
filter_kr_5m_composite_market_regime.py:31-43 의 'pf_target_tighter_slots1'
구성과 동일.

alpha-hunter import 0 — pandas + 표준 라이브러리만.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as _date
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class ExitProfile:
    stop_loss_pct: float
    take_profit_pct: Optional[float]
    trail_activation_pct: float
    trail_pct: float
    max_hold_days: int
    exit_hhmm: int = 1430
    weakness_exit_enabled: bool = True
    weakness_exit_hhmm: int = 1430


@dataclass(frozen=True)
class Signal:
    source: str
    variant: str
    ticker: str
    entry_ts: pd.Timestamp
    entry_price: float
    stop_price: float
    profile: ExitProfile
    priority: float
    score: float = 0.0

    @property
    def date(self) -> _date:
        return pd.Timestamp(self.entry_ts).date()

    @property
    def entry_hhmm(self) -> int:
        ts = pd.Timestamp(self.entry_ts)
        return ts.hour * 100 + ts.minute

    @classmethod
    def from_raw(
        cls,
        *,
        source: str,
        variant: str,
        ticker: str,
        entry_ts,
        entry_price: float,
        profile: ExitProfile,
        priority: float,
        explicit_stop: Optional[float] = None,
        score: float = 0.0,
    ) -> Optional["Signal"]:
        """raw 신호를 정규화해 Signal 반환. 비유효 시 None.

        - entry_price <= 0 또는 NaN/inf 이면 None.
        - explicit_stop 이 주어지면 max(explicit_stop, computed_stop) 채택
          (computed_stop = entry_price * (1 - stop_loss_pct/100)).
        - 최종 stop_price >= entry_price 면 None.
        """
        if not pd.notna(entry_price) or not math.isfinite(entry_price) or entry_price <= 0:
            return None
        computed_stop = entry_price * (1 - profile.stop_loss_pct / 100.0)
        stop_price = computed_stop if explicit_stop is None else max(
            float(explicit_stop), computed_stop
        )
        if stop_price >= entry_price:
            return None
        return cls(
            source=source,
            variant=variant,
            ticker=ticker,
            entry_ts=pd.Timestamp(entry_ts),
            entry_price=float(entry_price),
            stop_price=float(stop_price),
            profile=profile,
            priority=float(priority),
            score=float(score),
        )


@dataclass(frozen=True)
class CompositeConfig:
    """frozen + hashable.

    hhmm_ranges/allowed_hhmms 는 tuple-of-tuples 로 보관해 hash() 가능. dict-like
    조회는 hhmm_range_for() / allowed_set_for() 헬퍼 사용. 빈 tuple = 미적용.
    """

    label: str
    variants: frozenset[str]
    max_positions: int
    hhmm_ranges: tuple[tuple[str, int, int], ...] = ()
    allowed_hhmms: tuple[tuple[str, frozenset[int]], ...] = ()

    def hhmm_range_for(self, variant: str) -> Optional[tuple[int, int]]:
        for v, start, end in self.hhmm_ranges:
            if v == variant:
                return (start, end)
        return None

    def allowed_set_for(self, variant: str) -> Optional[frozenset[int]]:
        for v, allowed in self.allowed_hhmms:
            if v == variant:
                return allowed
        return None


# alpha-hunter filter_kr_5m_composite_market_regime.py:31-43 와 동치
BASE_CONFIG = CompositeConfig(
    label="pf_target_tighter_slots1",
    variants=frozenset({"reclaim_strict", "orb_event_quality", "native_close_top15"}),
    max_positions=1,
    hhmm_ranges=(("orb_event_quality", 1000, 1020),),
    allowed_hhmms=(
        ("reclaim_strict", frozenset({935, 940, 945, 955, 1000, 1005, 1010, 1015, 1025})),
        ("native_close_top15", frozenset({1025})),
    ),
)
