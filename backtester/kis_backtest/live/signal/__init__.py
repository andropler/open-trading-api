"""Signal subpackage: 도메인 모델 + selector + engine 인터페이스.

5m Composite 전략의 진입 신호 표현·선택·엔진 추상화. 실제 엔진 구현
(Reclaim/ORB/Native5mBreakout)은 후속 이터레이션에서 alpha-hunter 코드를
포팅하여 SignalEngine Protocol 을 만족하도록 추가한다.
"""

from kis_backtest.live.signal.engine import SignalEngine, compose_signals
from kis_backtest.live.signal.models import (
    BASE_CONFIG,
    CompositeConfig,
    ExitProfile,
    Signal,
)
from kis_backtest.live.signal.selector import select_signals

__all__ = [
    "BASE_CONFIG",
    "CompositeConfig",
    "ExitProfile",
    "Signal",
    "SignalEngine",
    "compose_signals",
    "select_signals",
]
