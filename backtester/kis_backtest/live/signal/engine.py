"""SignalEngine Protocol + 다엔진 통합 헬퍼 (compose_signals).

라이브 트레이더는 여러 엔진(Reclaim/ORB/Native)을 병렬 보유하고, 매일 장 시작
전 candidate_signals(asof_date) 를 호출해 후보 신호를 모은 뒤 select_signals
로 BASE_CONFIG 필터를 적용한다. 실제 엔진 구현은 후속 이터레이션.
"""

from __future__ import annotations

from typing import Iterable, Protocol

import pandas as pd

from kis_backtest.live.signal.models import CompositeConfig, Signal
from kis_backtest.live.signal.selector import select_signals


class SignalEngine(Protocol):
    """라이브 5m 신호 엔진 인터페이스.

    구현체 계약:
    - candidate_signals(asof_date) 는 같은 asof_date 입력에 대해 여러 번 호출될
      수 있으며, 동일 입력에는 동일(또는 동치인) 결과를 반환해야 한다.
      (compose_signals + dry_run_trade_step 의 raw_total 계산이 이중 호출 가정)
    - side effect 가 있다면 (DB/API 호출) 호출자가 비용을 지불할 수 있도록
      문서화하거나 내부 캐시로 멱등성을 보장해야 한다.
    """

    @property
    def name(self) -> str: ...

    def candidate_signals(self, asof_date: pd.Timestamp) -> list[Signal]: ...


def compose_signals(
    engines: Iterable[SignalEngine],
    asof_date: pd.Timestamp,
    config: CompositeConfig,
) -> list[Signal]:
    """엔진들의 후보 신호를 모아 config 필터 적용 후 반환."""
    raw: list[Signal] = []
    for engine in engines:
        raw.extend(engine.candidate_signals(asof_date))
    return select_signals(raw, config)
