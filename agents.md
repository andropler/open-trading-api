# open-trading-api Agent Guide

이 문서는 이 저장소에서 전략 구현, 백테스트, 자동매매 기능을 확장할 때 미래의 코딩 에이전트가 따라야 할 작업 기준이다.

## 1. 프로젝트의 실제 역할

이 저장소는 단일 앱이 아니라 아래 4개 성격이 섞인 작업공간이다.

| 경로 | 역할 | 자동매매 관점의 해석 |
| --- | --- | --- |
| `strategy_builder/` | 전략 설계, 시그널 생성, 주문 실행 UI/백엔드 | 실시간 매매에 가장 직접적으로 연결되는 영역 |
| `backtester/` | QuantConnect Lean 기반 백테스트 엔진 | 전략 검증과 파라미터 탐색 담당 |
| `examples_user/` | 자산군별 통합 API 예제 | 실제 KIS API 호출 패턴 참고용 |
| `examples_llm/` | 기능 단위 API 샘플 | 단일 API 탐색 및 빠른 프로토타입 참고용 |

`legacy/`는 현재 설계의 중심이 아니다. 특별한 이유가 없으면 읽기 전용 참고 자료로 취급한다.

## 2. 자동매매 시스템으로 볼 때의 핵심 결론

- 현재 실시간 주문 경로의 중심은 `strategy_builder`다.
- 현재 백테스트 경로의 중심은 `backtester`다.
- 저장소 전체는 여러 자산군 샘플을 포함하지만, 실시간 전략 실행 코드는 사실상 국내주식 기준으로 짜여 있다.
- 장기적으로는 `strategy_builder`를 "실시간 실행 계층", `backtester`를 "검증 계층"으로 분리해서 생각하는 것이 맞다.

## 3. 가장 먼저 알아야 할 제약사항

- `strategy_builder/core/signal.py`는 종목코드를 6자리로 강제한다. 현재 실시간 시그널 구조는 국내주식 전제다.
- `strategy_builder/core/data_fetcher.py`의 전략 데이터 조회는 국내주식 일봉 REST API 기반이다. 현재 신호 생성은 일봉 중심이다.
- `strategy_builder/core/order_executor.py`의 기본 매수 수량은 1주다. 별도 수량을 주지 않으면 포지션 사이징이 없다.
- 같은 파일의 기본 매도 수량은 보유 전량이다. 부분 청산 로직은 기본 제공되지 않는다.
- `strategy_builder/core/risk_manager.py`는 존재하지만, 현재 기본 주문 실행 경로에서 적극적으로 적용되지 않는다.
- `strategy_builder/core/websocket_manager.py`는 진짜 체결/호가 웹소켓이 아니라 REST 폴링 기반이다.
- `strategy_builder/backend/routers/symbols.py`의 종목 검색용 마스터 수집은 코스피/코스닥 중심이다.
- `backtester`는 Docker 기반 Lean 엔진이 필요하다.
- `backtester/pyproject.toml`에는 pytest 설정이 있지만 현재 `backtester/tests/` 디렉터리는 없다. 즉 테스트 인프라는 선언되어 있고 실제 테스트는 거의 비어 있다.

## 4. 실거래 전에 반드시 다시 확인할 리스크

- `strategy_builder/backend/state.py`는 모드를 `vps`/`prod`로 관리한다.
- `strategy_builder/core/order_executor.py`는 실전 주문 TR 선택에서 `real` 값을 기준으로 분기한다.
- 따라서 실전 주문 모드 명칭이 계층마다 완전히 일치하지 않는다. 실거래 투입 전 이 경로는 반드시 재검증하거나 정리해야 한다.
- 이 저장소는 "전략 생성 + 실행 예제"로는 유용하지만, 그대로 운영형 자동매매 시스템이라고 보기에는 부족하다.
- 운영형 시스템에 필요한 스케줄러, 영속 상태 저장, 주문 중복 방지, 장애 복구, 알림, 감사 로그, 포트폴리오 규칙은 별도 설계가 필요하다.

## 5. 전략 구현 시 권장 작업 순서

1. 아이디어를 먼저 `.kis.yaml` 또는 `backtester` 전략 DSL로 표현한다.
2. `backtester`에서 기간, 종목군, 손절/익절 파라미터를 검증한다.
3. 검증이 끝난 전략만 `strategy_builder` 실시간 전략으로 옮긴다.
4. 실시간 계층에서는 주문 수량, 최대 포지션 수, 재진입 제한, 손절 우선순위를 별도로 보강한다.
5. 마지막에 스케줄러나 워커 프로세스를 붙여 자동 실행한다.

현재 구조상 "전략 정의"와 "운영 자동화"는 별개의 일이다. 전략 하나를 추가했다고 자동매매 시스템이 완성되지는 않는다.

## 6. 새 전략을 추가할 때 수정할 곳

### 6.1 실시간 실행용 전략 추가 (`strategy_builder`)

아래 순서를 기본으로 한다.

1. `strategy_builder/strategy/strategy_xx_name.py`에 `BaseStrategy` 상속 클래스를 추가한다.
2. 전략 클래스 안에서는 데이터 조회와 지표 계산만 한다. 직접 주문 API를 호출하지 않는다.
3. `strategy_builder/strategy_core/preset/name.py`에 프리셋 등록 객체를 추가한다.
4. `strategy_builder/strategy_core/preset/__init__.py`에 import를 추가해 자동 등록되게 만든다.
5. 빌더에서도 같은 전략을 다루려면 `builder_state`, `params`, `param_map`을 함께 정의한다.
6. 새 지표가 필요하면 최소한 `strategy_builder/core/indicators.py`, `strategy_builder/strategy_core/dsl/converter.py`, `strategy_builder/strategy_core/dsl/codegen.py`를 같이 점검한다.
7. UI에서 지표를 직접 선택해야 하면 `strategy_builder/frontend/src/lib/builder/constants.ts`도 업데이트한다.

핵심 원칙은 "실시간 실행 전략"과 "빌더 메타데이터"를 같이 유지하는 것이다. 둘 중 하나만 바꾸면 UI와 실행 계층이 쉽게 어긋난다.

### 6.2 백테스트용 전략 추가 (`backtester`)

1. `backtester/kis_backtest/strategies/preset/*.py`에 `BaseStrategy` 상속 클래스를 추가한다.
2. `PARAM_DEFINITIONS`를 정의해서 파라미터 메타데이터를 한 곳에서 관리한다.
3. `build()`가 `StrategyDefinition`을 반환하도록 만든다.
4. `backtester/kis_backtest/strategies/preset/__init__.py`에 import를 추가해 자동 등록되게 만든다.
5. 필요하면 `to_lean_params()` 또는 커스텀 Lean 코드 생성을 보강한다.

`backtester` 쪽은 선언적 정의가 중심이고, `strategy_builder` 쪽은 실행 가능한 Python 전략 클래스가 중심이다. 두 계층은 비슷해 보여도 구현 방식이 다르다.

## 7. 두 계층 사이의 연결 포맷

- 공통 계약은 `.kis.yaml`이다.
- `strategy_builder`는 `builder_state -> DSL -> Python 전략 코드` 흐름을 가진다.
- `backtester`는 `.kis.yaml -> StrategyDefinition -> Lean 코드` 흐름을 가진다.
- 자동매매용 전략을 장기적으로 유지하려면 Python 클래스보다 `.kis.yaml` 또는 선언형 정의를 먼저 진실 원본으로 두는 편이 관리가 쉽다.

## 8. 프리셋 이름/ID는 계층마다 완전히 같지 않다

아래 매핑을 기억한다.

| 실시간 쪽 `strategy_builder` | 백테스트 쪽 `backtester` |
| --- | --- |
| `golden_cross` | `sma_crossover` |
| `momentum` | `momentum` |
| `week52_high` | `week52_high` |
| `consecutive` | `consecutive_moves` |
| `disparity` | `ma_divergence` |
| `breakout_fail` | `false_breakout` |
| `strong_close` | `strong_close` |
| `volatility` | `volatility_breakout` |
| `mean_reversion` | `short_term_reversal` |
| `trend_filter` | `trend_filter_signal` |

같은 전략을 양쪽에 추가할 때는 이름이 아니라 "의미와 조건"을 맞추는 것이 우선이다.

## 9. 이 저장소에서 시간을 가장 아껴 주는 파일들

- `README.md`: 전체 구조와 각 서브프로젝트의 역할
- `strategy_builder/README.md`: 실시간 실행 흐름과 UI 구조
- `backtester/README.md`: 백테스트 흐름과 운영 조건
- `strategy_builder/backend/routers/strategy.py`: 실시간 전략 실행 진입점
- `strategy_builder/core/order_executor.py`: 실제 주문 실행 로직
- `strategy_builder/core/data_fetcher.py`: 시세/잔고/일봉 조회
- `strategy_builder/strategy_core/executor.py`: 프리셋, 로컬 전략, 커스텀 전략의 통합 실행기
- `backtester/backend/routes/backtest.py`: 백테스트 API 진입점
- `backtester/kis_backtest/strategies/registry.py`: 백테스트 전략 등록부
- `backtester/examples/live_trading.py`: 장기적으로 별도 자동매매 워커를 만들 때 참고할 수 있는 라이브 트레이딩 예제

## 10. 운영 자동매매를 만들 때 필요한 추가 작업

이 저장소만으로는 부족한 부분이 많다. 실제 자동매매 시스템을 만들 때는 아래를 별도 설계 대상으로 본다.

- 항상 켜져 있는 전략 실행 워커 프로세스
- 정해진 주기마다 전략을 실행하는 스케줄러 또는 데몬
- 주문 중복 방지 키와 재시도 정책
- 포트폴리오 단위 포지션 사이징
- 종목별 최대 손실, 일일 손실 한도, 총 익스포저 제한
- 체결 확인 후 상태 동기화
- 장 시작 전/장중/장마감 후 모드별 분기
- 장애 알림, 로그 적재, 주문 감사 추적

이 저장소는 "전략 개발 플랫폼"에 가깝고, 운영계 자동매매는 그 위에 한 층 더 만들어야 한다.

운영형 자동매매를 추가할 때는 `strategy_builder/backend/routers/*.py` 안에 장시간 루프나 스케줄러를 직접 넣지 않는 편이 좋다. 별도 워커 모듈이나 별도 프로세스로 분리하고, FastAPI는 수동 제어와 상태 조회 API에 가깝게 유지한다.

## 11. 추천 실행 명령

루트 의존성 설치:

```bash
uv sync
```

전략 빌더 실행:

```bash
cd strategy_builder
./start.sh
```

백테스터 실행:

```bash
cd backtester
./start.sh
```

라이브 트레이딩 예제 확인:

```bash
cd backtester
uv run python examples/live_trading.py --example manual
```

## 12. 작업 우선순위 제안

이 저장소를 기반으로 자동매매 시스템을 확장할 때 우선순위는 아래가 좋다.

1. `strategy_builder` 주문 모드와 수량 결정 로직을 정리한다.
2. 공통 리스크/포지션 규칙을 한 계층으로 모은다.
3. `.kis.yaml` 기반 전략 정의를 진실 원본으로 고정한다.
4. `backtester`와 `strategy_builder` 사이 전략 의미가 어긋나지 않게 매핑을 문서화한다.
5. 마지막에 스케줄러와 운영 로깅을 추가한다.

이 문서의 목표는 "어디를 고쳐야 하는지 빠르게 판단하게 하는 것"이다. 새 기능을 만들 때는 먼저 실시간 계층인지, 백테스트 계층인지, 샘플 API 계층인지부터 분리해서 생각한다.
