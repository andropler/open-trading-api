"""신호 선택기.

alpha-hunter research_kr_5m_composite_strategy.py:636-651 의 _select_signals 와
동치. variant 매칭 + hhmm_ranges + allowed_hhmms 필터를 모두 AND 로 적용.
"""

from __future__ import annotations

from kis_backtest.live.signal.models import CompositeConfig, Signal


def select_signals(signals: list[Signal], config: CompositeConfig) -> list[Signal]:
    selected: list[Signal] = []
    for sig in signals:
        if sig.variant not in config.variants:
            continue
        hhmm_range = config.hhmm_range_for(sig.variant)
        if hhmm_range is not None:
            start, end = hhmm_range
            if not start <= sig.entry_hhmm <= end:
                continue
        allowed = config.allowed_set_for(sig.variant)
        if allowed is not None and sig.entry_hhmm not in allowed:
            continue
        selected.append(sig)
    return selected
