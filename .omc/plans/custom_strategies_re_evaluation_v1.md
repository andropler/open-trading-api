# Custom Strategies — 재검증·개선 작업 플랜 v1

대상: `backtester/kis_backtest/custom/` 의 사용자 정의 전략 3종
- `kr_intraday_breakout_v41` (1H, 점수 4/10)
- `kr_5m_composite_mbull2060` (5m+1m, 점수 5/10)
- `kr_intraday_orb_5m` (5m, 점수 2/10)

기준: `backtester/kis_backtest/custom/VALIDATION.md` (검증일 2026-05-05)

작업일: 2026-05-05  ·  작업자: 본인  ·  목적: **연구 (라이브 투입 안 함)**

---

## 1. 요건 요약

사용자 의도: VALIDATION.md의 점수와 약점 진단을 **냉정하게 다시 한 번 도전(challenge)**해서, 점수가 진짜로 그 정도가 맞는지 확인하고, 가능한 경우 점수를 끌어올리는 개선까지 진행한다. **결과물은 리포트 중심**, 검증을 위한 임시 스크립트는 짜되 코드 변경은 명백히 정당화될 때만.

핵심 기대 산출물: `backtester/kis_backtest/custom/VALIDATION_v2.md` (또는 `ANALYSIS_2026-05-05.md`) — 5개 검증 이슈에 대한 정량 결과·재평가 점수·라이브 가능성 재판정.

---

## 2. 인수 기준 (Acceptance Criteria, 모두 testable)

### A. 정량 산출물
- [ ] **A1.** V4.1 TRAIN 구간(2023-03~2024-12) cost stress 표 (cost ∈ {0.30, 0.55, 0.80, 1.00, 1.50}) — PF·승률·수익·MDD 5개 칼럼.
- [ ] **A2.** Composite m_bull_20_60 regime의 daily-근사 약세장 결과 (2018-01~2022-12) — KODEX 200 기준일 분포, "관망 일수 / 매매 일수", `069500` 자체 long-only 적용 시 PF.
- [ ] **A3.** Daily 데이터에서 폐지 종목 포함 vs 미포함 PF 차이 측정 — V4.1과 같은 패턴(돌파+VWAP·Volume 필터)을 daily-근사로 단순화한 버전으로 PF gap 정량화. 5~15% 부풀림 추정의 lower/upper bound.
- [ ] **A4.** V4.1 파라미터 그리드 재탐색 결과 — 진짜 OOS(2023-03~2024-12) 기준 robustness 메트릭 상위 10조합. 디폴트 PF 1.28 vs 그리드 best PF 비교.
- [ ] **A5.** ORB 진로 결정 1페이지 — 폐기 / Composite 흡수 / 단독 운용의 trade-off 표 + 결론.

### B. 재평가 점수
- [ ] **B1.** 3개 전략에 대한 새 10점 만점 점수 (이전 점수와 변경 사유 명시).
- [ ] **B2.** 라이브 가능성 재판정 (현 시점에선 의미 없음 — 연구 목적이지만 학술적 결론으로 기록).

### C. 우선순위 정렬
- [ ] **C1.** 5개 이슈가 **ROI(영향/노력) 우선순위**로 정렬되어 리포트 상단에 표시. P1~P5 라벨.

### D. 품질 게이트
- [ ] **D1.** 모든 수치는 재현 명령(임시 스크립트 경로 또는 in-process 호출 코드) 동봉.
- [ ] **D2.** 보고서의 모든 결론은 데이터 표에 직접 연결 (vague 표현 금지: "robust" → "TRAIN PF 1.x, TEST PF 1.y, gap 0.z").
- [ ] **D3.** in-sample fit 의심이 가는 모든 결과는 **명시적으로 그렇게 라벨링**.

---

## 3. 우선순위 (P1 → P5, ROI 기반)

| P | 이슈 | 영향 | 노력 | 근거 |
|---|------|:--:|:--:|------|
| **P1** | V4.1 진짜 OOS 비용 stress | 高 | 低 | 가장 공포스러운 미지수. 1시간이면 답이 나옴. 만약 PF<1이면 V4.1은 "라이브 후보" 자격 자체 박탈 → 점수 2~3으로 추락. 통과해도 "borderline" → "조건부 가능"으로 +1. |
| **P2** | Composite 약세장 OOS (daily 근사) | 高 | 中 | 점수 5/10의 가장 큰 약점이 "약세장 OOS 부재". regime 필터의 관망 능력만 daily로 검증해도 핵심 가설(강세장 한정 운용으로 약세 회피)의 진위가 갈림. 통과 시 +1~2, 실패 시 –2. |
| **P3** | V4.1 파라미터 그리드 재탐색 | 中 | 中 | alpha-hunter fit이 진짜 OOS에선 평범 → 다른 조합이 더 robust할 가능성. 발견 시 V4.1 점수 +1, 디폴트 변경 권고. 안 나와도 "현 디폴트가 fit 조합 중에선 best"라는 결론. |
| **P4** | 폐지종목 영향 정량화 (daily proxy) | 中 | 中~高 | 모든 인트라데이 결과가 **시스템적**으로 부풀려졌을 가능성 — 절대 점수 자체에 영향. 단, daily proxy가 1H/5m 결과에 직접 적용은 어려워 "추정 보정"에 그침. |
| **P5** | ORB 진로 결정 명문화 | 低 | 低 | 사실상 이미 결정됨(VALIDATION에서 부적합 판정). 명문화만 — 30분 작업. P1~P4 끝나고 정리 차원. |

> **순서 결정 원칙**: P1 결과가 V4.1 라이브 가능성을 좌우 → P3(V4.1 그리드)를 P2 앞에 둘지 고민했으나, P2가 Composite의 핵심 가설 검증이라 더 본질적. P3는 P1 결과에 따라 dynamic priority(예: P1 통과 못 하면 P3 자동 deprio).

---

## 4. 구현 단계 (Implementation Steps)

각 단계는 **임시 검증 스크립트 → 결과 표 → 리포트 섹션**의 패턴.

### Step 0. 인프라 준비 (15분)
- [ ] `backtester/scripts/validation_v2/` 디렉토리 생성 (임시 스크립트 모음, gitignore 가능)
- [ ] `backtester/kis_backtest/custom/VALIDATION_v2.md` 빈 파일 + 목차 골격
- [ ] V4.1·ORB 백테스터를 cost·기간 가변 인자로 호출하는 헬퍼 함수 (이미 `BreakoutV41Params`/`ORBParams`가 dataclass라 별도 헬퍼 거의 불필요 — 호출 패턴만 정리)

### Step 1. P1 — V4.1 진짜 OOS 비용 stress (30분~1시간)
참고: `backtester/kis_backtest/custom/kr_intraday_breakout.py:251` (`run(start_date=..., end_date=...)` 지원 확인됨)

- [ ] `KRIntradayBreakoutV41Backtester`를 cost ∈ {0.30, 0.55, 0.80, 1.00, 1.50} × 기간 = ('2023-03-01','2024-12-31')(TRAIN) 5회 실행
- [ ] 표 작성: cost / trades / 승률 / 수익% / MDD% / PF / Sharpe
- [ ] 비교: TEST 표(VALIDATION.md §2)와 같은 포맷으로 나란히 배치
- [ ] **결론 분기**:
  - PF≥1.0 @ cost=1.0% → "borderline 유지, 라이브 비용이 0.55%면 안전 마진"
  - PF<1.0 @ cost=1.0% → "실비용 1%에서 손실. 점수 4 → 3으로 하향, 라이브 후보에서 제외"
- [ ] 리포트 §1에 결과 + 결론 기록

### Step 2. P2 — Composite 약세장 OOS (daily 근사) (1.5~2시간)
이 단계가 가장 까다로움. 5m 신호를 daily로 재현하는 게 아니라 **regime 필터의 "관망 결정 능력"**만 본다.

- [ ] daily 데이터(`/Users/benjamin/personal_workspace/shared_data/kr_stocks/daily/`)에서 KODEX 200(`069500`) 로드 + m_bull_20_60 plug-in 작성
   - 전일 close > SMA20 AND SMA20 > SMA60 AND 5d return > 0
- [ ] 2018-01~2022-12 (5년) 일별 regime 플래그 산출
- [ ] 각 연도별 매매일 / 관망일 분포 — "약세장에서 자동 관망률" 계산
   - 2018 (Q4 폭락), 2020 (Q1 코로나), 2022 (전반적 약세) 등 known 약세 구간에서 관망 비율 높은지
- [ ] "**KODEX 200 자체에 m_bull_20_60 long-only 적용**" 단순 백테스트 (proxy)
   - 신호일에 종가 매수, 비신호일 cash → daily PF·MDD
   - vs Buy & Hold 069500 → regime 필터의 부가가치 측정
- [ ] **결론 분기**:
  - 약세 구간에서 관망률 70%+ AND proxy MDD < B&H MDD → "regime 필터 작동 확인, Composite 5/10 → 6/10"
  - 약세 구간 관망률 50% 미만 OR proxy MDD ≥ B&H MDD → "regime 필터 효과 약함, Composite 5 → 3~4"
- [ ] 리포트 §2에 기록 + 한계 명시 (5m 신호 자체는 약세장 데이터 없어 검증 불가)

### Step 3. P3 — V4.1 파라미터 그리드 재탐색 (2~3시간)
**효율 보호**: 그리드를 10⁵+ 조합으로 만들지 말 것. 핵심 4개 차원만.

- [ ] 그리드 정의 (총 ~80~150 조합):
  - `breakout_lookback` ∈ {3, 4, 5, 6}
  - `vol_multiplier` ∈ {1.5, 2.0, 2.5, 3.0}
  - `trail_pct` ∈ {0.3, 0.5, 0.8, 1.0}
  - `sl_pct` ∈ {3.0, 5.0, 7.0}
- [ ] 각 조합을 TRAIN(2023-03~2024-12)에서 실행, cost=0.55%
- [ ] robustness 메트릭: PF * (1 - |Sharpe gap to TEST PF|) — 단순 PF 최대화 회피
- [ ] 상위 10조합 표 + 디폴트(현재 파라미터) 위치 표시
- [ ] **결론 분기**:
  - 디폴트가 top 5 안 → "현 파라미터가 robust. 변경 권고 없음."
  - 더 나은 조합 발견(robustness 메트릭 +20% 이상) → 권고 + "라이브 검토 시 후보 파라미터" 라벨
- [ ] **주의**: 발견된 best 조합을 TRAIN에서만 평가하면 in-sample fit. 반드시 TEST에서도 검증해서 "TRAIN-fit이 아닌 진짜 robust"인지 확인. 이중 통과 못 하면 보고만 하고 권고하지 말 것.
- [ ] 리포트 §3 기록

### Step 4. P4 — 폐지종목 영향 정량화 (daily proxy) (2~3시간)
- [ ] daily 데이터의 폐지 종목 후보 리스트 산출 (마지막 거래일이 데이터 마감일 + 1년 이상 전인 ticker, 또는 known 케이스: 한진해운·STX조선 등)
- [ ] V4.1 패턴을 daily 근사로 단순화한 simplified backtester (1~2시간 봉 → 일봉 — 별도 전략이지만 폐지 영향 측정용 proxy)
   - 일봉 돌파(전일 high), volume ≥ 평균×2, close ≥ 5000 → 다음 날 시초 매수
- [ ] 같은 기간(2018~2022)에 대해 **ALL_TICKERS** vs **NON_DELISTED_ONLY** 두 백테스트
- [ ] PF·CAGR·MDD 차이 → "폐지 종목 제외 시 X% 부풀림" 정량화
- [ ] 결과를 **인트라데이 결과의 보정 추정치**로 사용 (1H/5m 데이터에 직접 적용 불가하지만 "5~15% 부풀림" 진술의 실증적 근거)
- [ ] 리포트 §4 기록

### Step 5. P5 — ORB 진로 결정 (30분)
- [ ] 표: ORB 단독 / Composite 흡수(orb_event_quality) / 폐기 — trade-off
- [ ] 결정: VALIDATION.md §6 권고("단독 운용 금지, composite의 ORB variant로만") 그대로 명문화
- [ ] 후속 권고: `kr_intraday_orb_5m`을 `custom/__init__.py`에서 export는 유지하되 README.md와 VALIDATION_v2.md에 "**baseline / 비교용. 단독 운용 부적합.**" 경고 명시
- [ ] 리포트 §5 기록

### Step 6. 종합 — 재평가 점수 + 라이브 가능성 재판정 (45분)
- [ ] 3개 전략 새 10점 만점 점수 + 변경 사유
- [ ] VALIDATION.md vs VALIDATION_v2.md 비교 표
- [ ] 결론: 연구 시각에서 "**가장 견고한 한 종**"과 "**버려야 할 한 종**" 명시
- [ ] 부록: 재현 명령 모음

### Step 7. (선택) 코드 변경 (이슈별 결정)
원칙: P3 결과가 새 디폴트를 권고할 때만 코드 수정. 그 외에는 **README.md와 VALIDATION_v2.md만** 업데이트.

- [ ] (조건부) `kr_intraday_breakout.py:48-67` `BreakoutV41Params` 디폴트 — P3에서 더 robust한 조합 발견 + TRAIN/TEST 이중 통과 시에만
- [ ] (조건부) `intraday_orb.py` — 폐기 결정 시 모듈 자체 삭제 / 보존 시 docstring에 "baseline only" 추가
- [ ] `custom/README.md` — VALIDATION_v2.md 링크 추가, 이전 표는 stale 표시

---

## 5. 리스크 & 완화

| 리스크 | 영향 | 완화 |
|--------|:--:|------|
| Step 3 그리드 결과를 in-sample fit으로 인지 못 하고 "발견" 선언 | 높음 | TRAIN/TEST 이중 통과 게이트 강제. 단일 best는 보고만. |
| Step 2의 daily proxy를 5m 신호의 약세 검증으로 오해 | 중간 | "regime 필터의 관망 능력만 검증, 5m 신호 자체는 검증 불가" 라벨 강제. |
| Step 4 폐지종목 daily proxy를 인트라데이 결과에 직접 적용 | 중간 | "추정 보정치"로만 표기, 점수 자체 변경엔 사용하지 않음. |
| 시간 초과로 P4·P5만 못 끝냄 | 낮음 | 우선순위가 낮은 단계라 P1~P3까지만 끝나도 핵심 가치 확보. |
| P1에서 V4.1이 "라이브 후보 자격 박탈"되면 P3 의미 약화 | 중간 | P3는 "현 디폴트가 그리드 안에서 best 위치인가"라는 학술적 가치만으로도 의미 있음 — 진행. |
| 임시 검증 스크립트 누적으로 repo 더러워짐 | 낮음 | `backtester/scripts/validation_v2/`에 모은 후 `.gitignore` 또는 `tests/`로 이동. |

---

## 6. 검증 단계 (Verification)

리포트 머지 전 self-review 체크리스트:

- [ ] 각 결론이 표·수치에 직접 연결되는가?
- [ ] "robust", "강건", "약함" 같은 단어 옆에 정량 메트릭이 붙어 있는가?
- [ ] in-sample / out-of-sample 라벨이 모든 결과 옆에 명시되었는가?
- [ ] 새 점수가 옛 점수와 다르면 변경 사유가 동일 페이지에 있는가?
- [ ] 재현 명령이 실제 동작하는가? (대표 1건 즉석 재실행)
- [ ] **선택**: critic 에이전트로 리포트 1차 리뷰 (옵션 — 사용자 요청 시).

---

## 7. 워크플로우 다이어그램

```
P1 (V4.1 OOS cost stress)  →  결과 분기
  ├─ 통과(PF≥1@1%) → P3 진행
  └─ 실패(PF<1@1%) → P3 deprio, 결론 "라이브 부적합"

P2 (Composite 약세 regime) →  결과 분기 (P1과 무관)
  ├─ 통과(관망 70%+, proxy MDD↓) → 점수 +1~2
  └─ 실패 → 점수 –2 + Composite 라이브 후보 박탈

P3 (V4.1 grid)  → 결과 분기
  ├─ 더 나은 조합(이중 통과) → 권고 + (옵션) 디폴트 변경 코드 PR
  └─ 디폴트가 best → 검증으로 종결

P4 (폐지종목 daily proxy) → 시스템적 보정 추정치 산출

P5 (ORB 진로) → 결정 명문화

→ 종합 리포트 VALIDATION_v2.md 작성
→ (선택) critic 1차 리뷰
→ 사용자 검토
```

---

## 8. 시간 견적

| 단계 | 견적 | 비고 |
|------|:--:|------|
| Step 0 | 0.25h | 디렉토리·골격 |
| Step 1 (P1) | 0.5~1h | run() 5번 호출 |
| Step 2 (P2) | 1.5~2h | regime 필터 daily 재현 + proxy 백테스트 |
| Step 3 (P3) | 2~3h | 80~150조합 grid + 이중 검증 |
| Step 4 (P4) | 2~3h | simplified daily 백테스트 + 폐지 vs 비폐지 비교 |
| Step 5 (P5) | 0.5h | 결정 명문화 |
| Step 6 (종합) | 0.75h | 점수 표 + 결론 |
| Step 7 (코드, 조건부) | 0.5~1h | P3 결과 의존 |
| **총합** | **8~12h** | 1~2일 작업량 |

---

## 9. 후속 작업 (Out of Scope)

이번엔 안 하지만 향후 필요한 것:

- 폐지 종목 1H/5m 데이터 보강 (외부 데이터 소스 필요 — 비용·시간 큼)
- Forward paper trading (라이브 안 한다는 사용자 결정에 따라 deprio)
- Composite 5m 신호의 진짜 약세장 OOS (5m 데이터셋이 2025-04~ 시작이라 데이터 자체 부재 — 시간 흐름이 답)
- 추가 전략 발굴 (다전략 분산용) — 본 작업의 결론 후에 별도 트랙으로

---

## 10. 변경 이력

- v1 (2026-05-05) — 초안 작성. 사용자 의도 인터뷰 결과 반영: 연구 목적, 5개 이슈 전부, 리포트 중심.
