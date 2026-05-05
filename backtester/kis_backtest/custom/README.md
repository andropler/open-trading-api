# 커스텀 전략 (`kis_backtest.custom`)

`kis_backtest`의 프리셋 10종(`strategies/preset/`)과 별개로, alpha-hunter 연구 워크스페이스에서
포팅·재구성한 **사용자 정의 인트라데이 전략 3종**을 모은 모듈입니다.

| 모듈 | 전략 ID | 봉 | 진입 스크립트 |
|------|---------|---|----------------|
| `kr_intraday_breakout.py` | `kr_intraday_breakout_v41` | 1H | `examples/kr_intraday_breakout_v41.py` |
| `kr_5m_composite_mbull2060.py` | `kr_5m_composite_mbull2060` | 5m 신호 + 1m 실행 | `examples/kr_5m_composite_mbull2060.py` |
| `intraday_orb.py` | `kr_intraday_orb_5m` | 5m | (예제 없음 — 직접 `IntradayORBBacktester` 호출) |

세 전략 모두 **로컬 parquet 데이터**(`/Users/benjamin/personal_workspace/shared_data/kr_stocks/`)를
직접 읽어 in-process로 시뮬레이션합니다. Lean Docker 의존성 없음.

> **비판적 검증 보고서**:
> - [VALIDATION.md](./VALIDATION.md) (v1, 2026-05-05) — 과적합·OOS·비용 stress·시기 robustness 검증 결과 및 10점 만점 점수.
> - [VALIDATION_v2.md](./VALIDATION_v2.md) (v2, 2026-05-05) — v1의 5개 후속 검증 이슈 정량 측정 + V4.1 파라미터 그리드 192 + universe 그리드 32. **점수 갱신**: V4.1 4→**6**, Composite 5→**6**, ORB 2→2.

---

## 1. KR 1H Breakout V4.1 (`kr_intraday_breakout_v41`)

alpha-hunter에서 검증된 1시간봉 돌파 전략의 standalone 포팅. 가장 robust한 한 종.

### 데이터·유니버스
- **봉**: 1시간봉 (`1h/{symbol}_1h.parquet`)
- **유니버스**: 매 거래일, 직전 5거래일 거래대금(close × volume, **10시 이후 봉만 합산**) 평균 상위 **15종**
- **랭킹 무누설**: `tv.shift(1).rolling(5).mean()` — 당일 거래대금은 절대 안 씀

### 진입 (10:00~11:00 1H 봉이 다음을 모두 만족 → **다음 봉 시가**에 매수)
- `high > 직전 4봉 최고가` (돌파)
- `volume ≥ 2.0 × 직전 20봉 평균 거래량` (거래량 확인)
- `close > VWAP` (당일 강세 위치)
- `close > open` (양봉)
- `close ≥ 5,000원` (저가주 제외)

### 청산 (1H 봉 단위 시뮬, 우선순위: stop > 추적stop > 시간)
- 손절 –5% (`entry × 0.95`)
- 진입가 대비 **+0.5%** 도달 시 trailing stop 활성화 → **고점 대비 –0.5%** 추적
- 14시 도달 시 종가 청산 (`max_hold_days=1`, 당일 청산)

### 포지션·비용
- 동시 보유 최대 **3종**, 자본 균등 배분
- 비용 **편도 0.55%** (수수료 + 세 + 슬리피지 round-trip 합산)

### 핵심 파라미터 (`BreakoutV41Params`)
```python
breakout_lookback=4    vol_multiplier=2.0    vol_avg_window=20
sl_pct=5.0             trail_pct=0.5         trail_activation=0.5
top_n_stocks=15        ranking_window=5
entry_hour_start=10    entry_hour_end=11     exit_hour=14
require_vwap=True      require_bullish_bar=True
min_price=5000.0       cost_pct=0.55
```

---

## 2. KR 5m Composite m_bull_20_60 (`kr_5m_composite_mbull2060`)

5분봉 신호 → 1분봉 실행으로 검증한 **3개 신호 패밀리 + 시장 regime 필터** 복합 전략.
사이드 워크스페이스 `alpha-hunter`의 검증된 신호 빌더와 1m 실행 시뮬레이터를 import해 사용.

### 시장 Regime 필터 (`m_bull_20_60`, 전일 정보만 사용 → look-ahead 안전)
KODEX 200(069500) 기준, 다음 **세 조건 모두** 만족하는 일자에만 매매:
- 전일 종가 > 전일 SMA20
- 전일 SMA20 > 전일 SMA60
- 전일 5일 수익률 > 0

조건 미충족 일자 → 관망. (강세장 한정 운용)

### 신호 패밀리 3종 (동시 후보 → 우선순위로 1 보유)

#### 2.1 `reclaim_strict` — Post-surge VWAP 재탈환
- **후보**: 최근 3일 +25~50% 급등 종목, gap 0~8%, 최저가 ≥ 5,000원
- **확정**: 09:35~10:25(허용 시각 한정) 5m 봉이 VWAP 재탈환 + 거래량 ≥ 80봉 평균 × 2.0
- **청산**: SL –3% / TP +10% / 활성화 +5% / 추적 –4% / 14:30 시간 청산

#### 2.2 `orb_event_quality` — 이벤트성 ORB
- **후보**: candidate_mode=event, **이벤트성 거래대금 ≥ 100억원**, 이벤트 거래량 ≥ 1.5배, gap 0~3%, 최저가 ≥ 5,000원
- **확정**: 09:00~09:30 30분 ORB 정의 → 10:00~10:20 사이 첫 돌파, **거래량 pace ≥ 3.0배**
- **청산**: SL –3% / TP +8% / 활성화 +4% / 추적 –3% / 14:30 시간 청산

#### 2.3 `native_close_top15` — 5m 거래대금 상위 15종 close 돌파
- **후보**: 5m 거래대금 상위 15종, 최저가 ≥ 5,000원
- **확정**: **10:25 단일 시점**에 직전 12봉(60분) 최고 종가 돌파, **같은 시간대** 평균 거래량 × 2.0
- **청산**: SL –5% / TP +10% / 활성화 +2% / 추적 –1.5% / `max_hold_days=1`

### 포지션·비용
- 동시 보유 **max_positions = 1** (단일 보유)
- 비용 **편도 0.55%** (round-trip)
- 시장 강세 + 단일 보유로 잡 신호 회피

### 폐기된 변형 (`discarded_candidates` in strategy.json)
`reclaim_loose`, `orb_recent_fast`, `native_high_scalp`, `no_regime_baseline` —
in-sample에서는 통과했으나 walk-forward에서 약화되어 제외.

---

## 3. KR 5m Opening Range Breakout (`kr_intraday_orb_5m`) — ⚠️ Baseline Only

> ⚠️ **단독 운용 부적합.** VALIDATION_v2.md §5 결정. cost 0.30% 이상에서 손실 전략이며,
> 한국 실비용 0.4~0.5% 환경에서 PF 0.5~0.7로 명백한 마이너스. 본 모듈은 5m Composite의
> `orb_event_quality` 패밀리(이벤트 거래대금 + regime 필터 결합 시 PF 2+) 비교용 baseline으로만 사용.

가장 단순한 5분봉 ORB 단일 패밀리. `kr_intraday_breakout.py`의 1H 패턴을 5m로 단순화.

### 데이터·유니버스
- **봉**: 5분봉 (`5m/{symbol}_5m.parquet`)
- **유니버스**: 매 거래일, 직전 5거래일 거래대금 평균 상위 **10종** (no look-ahead)

### Opening Range 정의
- 09:00~09:30 (6개 5분봉) → `OR_high` / `OR_low`
- **OR 폭 ≤ 8%** (폭이 너무 크면 false breakout 위험 ↑ → 제외)

### 사전 필터
- gap –5% ~ +20% (전일 종가 대비 시가)
- 최저가 ≥ 3,000원

### 진입
- ORB 이후 첫 **종가 > OR_high** 봉이 **10:00~10:30** 안에 발생할 때만 매수
- 09:30~10:00 조기 돌파는 **되밀림 손실원**으로 검증되어 제외
- (옵션) `min_volume_ratio` 필터 — 디폴트 0(비활성화)

### 청산 (5m 봉 단위 시뮬, low/high 보수적 평가)
- 손절 –3% / 익절 +8%
- 14:30 도달 시 종가 시간 청산

### 포지션·비용
- 동시 보유 최대 **3종**, 자본 균등 배분
- 비용 **편도 0.20%** (V4.1보다 낮은 가정 — 슬리피지 미포함)

### 핵심 파라미터 (`ORBParams`)
```python
or_minutes=30           sl_pct=3.0           tp_pct=8.0
entry_window_start="10:00"   entry_window_end="10:30"   exit_time="14:30"
top_n_stocks=10         ranking_window=5     cost_pct=0.20
min_price=3000.0        min_gap_pct=-5.0     max_gap_pct=20.0
max_or_width_pct=8.0    volume_avg_window=80 min_volume_ratio=0.0
```

---

## 검증 결과 (참고, 백테스트 시점 별도)

전체 메트릭은 시간이 지나면 데이터가 갱신되어 stale되므로 **재현 명령**과 함께 기록.

| 전략 | 기간 | Trades | 승률 | 누적수익 | Sharpe | MDD | PF |
|------|------|--:|--:|--:|--:|--:|--:|
| V4.1 | 2025-01 ~ 2026-03 | 179 | 60.3% | +71.7% | 2.20 | 14.5% | 1.69 |
| 5m Composite | 2025-06 ~ 2026-04 | 69 | 63.8% | +161.8% | 2.15 | 13.4% | 2.45 |
| ORB | 2025-04 ~ 2026-04 | 185 | 43.8% | +14.4% | 0.81 | 17.4% | 1.23 |

### 시기 robustness (연도별 PF)

| 전략 | 2025 PF | 2026 PF | 평가 |
|------|--:|--:|------|
| V4.1 | 1.49 | 2.38 | ✓ 일관성 양호 |
| 5m Composite | 2.44 | 2.90 | ✓ 매우 우수 |
| ORB | 0.77 | 2.64 | ✗ 시기 의존성 큼 — 단독 운용 부적합 |

> ORB는 단독으로 약하지만, composite의 `orb_event_quality`처럼 **이벤트 거래대금 + 시장 regime 필터**를 추가하면 PF 2.0+로 살아납니다.

---

## 재현 명령

```bash
cd backtester

# V4.1
uv run python examples/kr_intraday_breakout_v41.py
#   → examples/output/kr_intraday_breakout_v41/

# 5m Composite (외부 alpha-hunter 의존 — sibling 워크스페이스 필요)
uv run python examples/kr_5m_composite_mbull2060.py
#   → examples/output/kr_5m_composite_mbull2060/

# ORB (예제 entry script 없음, 직접 호출)
uv run python -c "
from kis_backtest.custom.intraday_orb import IntradayORBBacktester, ORBParams
bt = IntradayORBBacktester(params=ORBParams())
bt.load_data().compute_rankings().precompute().run(initial_equity=10_000_000, max_positions=3)
print(bt.metrics())
"
```

산출물(`*_summary.json`, `*_trades.csv`, `*_report.html`)은 `backtester/examples/output/{전략}/` 아래.
