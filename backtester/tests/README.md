# 전략 평가 파이프라인

`kis_backtest` 프리셋 전략들을 동일한 데이터·유니버스·기간에서 일괄 평가해
**Production / Research / Deprecated**로 분류하기 위한 sweep 도구 모음입니다.

Lean Docker가 아닌 **로컬 parquet 데이터**(`/Users/benjamin/personal_workspace/shared_data/kr_stocks/`)를
직접 읽어 빠르게 실행되는 in-process 백테스트를 사용합니다.

---

## 파이프라인 개요

```
                                 examples/output/sweep/
 ┌──────────────────┐           ┌─────────────────────────────┐
 │ strategy_sweep   │ phase1 ─▶ │ phase1_daily_top{N}_*.csv   │
 │ (디폴트 파라미터) │           └─────────────────────────────┘
 └──────────────────┘
                                 ┌─────────────────────────────┐
 ┌──────────────────┐    ──────▶ │ grid_top{N}_*.csv           │
 │ strategy_grid_   │            └─────────────────────────────┘
 │ sweep            │ ──┐
 │ (그리드 최적화)  │   │
 └──────────────────┘   │        ┌─────────────────────────────┐
                        ├──────▶ │ walk_forward_*.csv           │  ← walk_forward_validation
                        │        │  (IS 5y vs OOS 3.3y)         │
                        │        └─────────────────────────────┘
                        │
                        │        ┌─────────────────────────────┐
                        └──────▶ │ grid_best_validation_*.csv  │  ← grid_best_validation
                                 │  (50종 → 200종 OOS-by-size) │
                                 └─────────────────────────────┘

 ┌──────────────────┐
 │ strategy_classify│ ◀── 위 모든 CSV 입력 ──▶ CLASSIFICATION.md
 └──────────────────┘
```

각 단계는 **이전 단계의 CSV 산출물**을 `examples/output/sweep/`에서 글롭으로 자동 픽업합니다.
중간에 실패하거나 빠진 단계가 있어도 다음 단계는 가능한 만큼 진행합니다.

---

## 파일 역할

### 평가 러너 (`backtester/tests/`)

| 파일 | 역할 | 핵심 출력 |
|------|------|-----------|
| `strategy_sweep.py` | **디폴트 파라미터 sweep** — 등록된 10개 프리셋을 동일 유니버스·기간으로 일괄 백테스트. `phase1`(8년 일봉)과 `smoke`(1년) 서브커맨드 제공. | `phase1_daily_top{N}_*.csv`, `smoke_top{N}_*.csv` |
| `strategy_grid_sweep.py` | **핵심 파라미터 그리드 최적화** — 전략별 핵심 파라미터 1~2개의 작은 그리드를 돌려 디폴트 대비 어디까지 끌어올릴 수 있는지 평가. 디폴트가 거래 0건인 전략을 살릴 수 있는지도 함께 검증. | `grid_top{N}_*.csv` |
| `walk_forward_validation.py` | **시간 분할 robustness 검증** — 그리드-best 파라미터를 `2018–2022`(in-sample, 5년)와 `2023–2026`(out-of-sample, 3.3년) 두 구간에 적용. \|ΔSharpe\|<0.3이면 ROBUST로 판정. | `walk_forward_*.csv` |
| `grid_best_validation.py` | **유니버스 크기 robustness 검증** — 50종에서 찾은 그리드-best 파라미터를 200종 × 8년으로 확장 적용해 universe-size OOS를 측정. | `grid_best_validation_*.csv` |
| `strategy_classify.py` | **최종 분류 리포트** — sweep + grid CSV를 읽어 디폴트 vs 그리드 최적을 비교하고 Production / Research / Deprecated 판정 + 사유를 markdown으로 출력. 폐기는 "디폴트와 그리드 모두 부적합"이어야만 확정. | `CLASSIFICATION.md` |

### 의존하는 인프라 (`backtester/kis_backtest/`)

| 파일 | 역할 |
|------|------|
| `providers/parquet/data.py` (`ParquetDataProvider`) | KIS 라이브 API 대신 로컬 parquet 파일을 읽어 `DataProvider` 프로토콜을 구현. `daily/{symbol}.parquet`, `5m/{symbol}_5m.parquet`, `1h/{symbol}_1h.parquet` 자동 인식. KOSPI 벤치마크는 KODEX 200(069500) ETF로 대용. |
| `providers/__init__.py` | `ParquetDataProvider`를 패키지 레벨로 노출 (sweep 러너에서 `from kis_backtest.providers import ParquetDataProvider`). |
| `utils/universe.py` | 유동성(평균 거래대금 = close × volume) 기반 상위 N종 추출. 작은 종목 제외로 통계 안정성 + 실전 가능성 확보. ETF 제외 옵션 포함. |
| `core/converters.py` | 프리셋의 `Condition` → `ConditionSchema` 변환에 `ScaledIndicator`(예: `MA × 0.95`) 지원 추가. `ma_divergence` 같은 전략의 그리드 평가에 필수. |
| `client.py` + `strategies/generator.py` | `LeanClient.backtest_strategy`에 `commission_rate` / `tax_rate` / `slippage` 오버라이드 인자 추가. 같은 전략을 비용 환경만 바꿔 재평가하는 시나리오에 사용. |

---

## 분류 기준 (8년 일봉 기준)

`strategy_classify.py`가 적용하는 기준입니다. 디폴트와 그리드-best 중 **더 높은 등급**으로 최종 판정.

| 등급 | Sharpe | MDD | Profit Factor | Trades |
|------|--------|------|---------------|--------|
| **Production** | ≥ 0.9 | ≤ 25% | ≥ 1.5 | ≥ 100 |
| **Research** | ≥ 0.3 | – | – | ≥ 50 |
| **Deprecated** | 위 두 조건 모두 미달 | | | |

폐기 확정은 **디폴트 + 그리드 모두 부적합**이어야 합니다 — 그리드로 살릴 여지가 있는 전략을 일찍 잘라내지 않기 위함입니다.

---

## 실행 예시

```bash
cd backtester

# 1) 빠른 점검 (1년 × 30종) — 파이프라인 동작 확인용
uv run python tests/strategy_sweep.py smoke --top 30

# 2) Phase 1 — 디폴트 파라미터 sweep (200종 × 8년, 10개 전략)
uv run python tests/strategy_sweep.py phase1 --top 200 \
    --start 2018-01-01 --end 2026-03-25

# 3) 그리드 최적화 (50종 × 8년)
uv run python tests/strategy_grid_sweep.py --top 50 \
    --start 2018-01-01 --end 2026-03-25

# 4) 시간 robustness — IS 5y vs OOS 3.3y
uv run python tests/walk_forward_validation.py

# 5) 유니버스 크기 robustness — 50종 → 200종 OOS-by-size
uv run python tests/grid_best_validation.py

# 6) 최종 분류 리포트
uv run python tests/strategy_classify.py
# → backtester/examples/output/sweep/CLASSIFICATION.md
```

> 모든 산출물은 `backtester/examples/output/sweep/`에 타임스탬프 파일명으로 저장되며,
> 다음 단계 러너는 글롭 패턴으로 가장 최신 CSV를 자동 선택합니다.

---

## 사전 준비

`/Users/benjamin/personal_workspace/shared_data/kr_stocks/{daily,5m,1h}` 경로에
parquet 데이터(컬럼: `timestamp, open, high, low, close, volume`)가 있어야 합니다.
경로를 다르게 쓰려면 `ParquetDataProvider(data_root=Path(...))`로 직접 주입하세요.
