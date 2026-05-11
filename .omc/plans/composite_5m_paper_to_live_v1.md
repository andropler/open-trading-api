# 5m Composite 전략: 페이퍼 → 라이브 전환 계획 v1

작성일: 2026-05-05
대상 전략: `kr_5m_composite_mbull2060` (m_bull_20_60 레짐, max_positions=1, base_config_label=`pf_target_tighter_slots1`)
플랜 파일: `.omc/plans/composite_5m_paper_to_live_v1.md`

---

## 1. 요구사항 요약 (Requirements)

5m Composite 단독 전략을 **KIS 모의투자(vps) 실시간 페이퍼**로 1주 운영하여 시뮬레이션과 실측 차이를 검증한 뒤, **소프트 게이트**(주문/체결/청산 흐름이 고장 없이 동작)를 통과하면 **소액 ~500만원으로 KIS 실전(prod) 라이브**로 전환한다. 전 과정은 별도 Windows PC의 **WSL Ubuntu**에서 상시 구동하며, **텔레그램 봇** + **로컬 대시보드(`localhost:8002`)**로 모니터링하고 **보수적 킬스위치**(일 -3% / 누적 -8% / 연속 3패 / 웹소켓 5분+ 단절)를 항상 활성화한다.

## 2. 사용자 결정 사항 (Decision Log)

| 항목 | 선택 | 비고 |
|---|---|---|
| 페이퍼 형태 | **KIS 모의투자(vps) 실시간** | `paper_run.py`의 백테스트 페이퍼와 별개 — 라이브와 동일 코드 경로 |
| 실행 환경 | **Windows PC + WSL Ubuntu** | systemd-on-WSL 가능, 단 Windows 전원·부팅 의존 |
| 초기 자본 | **~500만원 소액** | Composite는 max_positions=1이므로 종목당 ~500만원 배정 |
| 알림 채널 | **텔레그램 Bot + 로컬 대시보드(`8002`)** | 실시간은 텔레그램, 사후 분석은 대시보드 |
| 전환 게이트 | **1주 페이퍼 + 소프트 게이트** | "주문·체결·청산 흐름이 고장 없이" 기준 |
| 킬스위치 | **보수적** | -3%일 / -8%누적 / 연속 3패 / WS 5분+ 단절 시 HALT |
| 전략 범위 | **5m Composite 단독** | V4.1은 본 계획에서 제외 (병행은 차후 확장) |

## 3. 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────────┐
│  Windows PC (24/7)                                              │
│  ├─ Windows 작업 스케줄러: 부팅 시 WSL distro 자동 시작          │
│  └─ WSL Ubuntu                                                  │
│      ├─ systemd unit: composite-trader.service                  │
│      │   └─ Python: trader_loop.py                              │
│      │       ├─ KIS Auth (vps/prod, ~/KIS/config/KIS_MODE)      │
│      │       ├─ Signal Loader (alpha-hunter 매일 갱신)           │
│      │       ├─ Strategy Runner (5m bar close 시 평가)          │
│      │       ├─ Order Manager (KISBrokerageProvider)            │
│      │       ├─ Position Tracker (~/KIS/state/positions.json)   │
│      │       ├─ Risk/Killswitch (한도 감시)                     │
│      │       └─ WebSocket (시세 + 체결통보)                     │
│      ├─ systemd unit: composite-signal.service (cron-like)      │
│      │   └─ 매일 08:30 KST: alpha-hunter 신호 재생성             │
│      ├─ systemd unit: composite-dashboard.service               │
│      │   └─ uvicorn :8002 (start.sh 백엔드 재활용)              │
│      └─ systemd unit: composite-heartbeat.service               │
│          └─ 60초마다 ~/KIS/state/heartbeat.json 갱신             │
│             5분 미갱신 감지 시 텔레그램 CRITICAL 1회 발송         │
└─────────────────────────────────────────────────────────────────┘
        │                              ▲
        │ orders/quotes (HTTPS+WSS)    │ alerts (HTTPS)
        ▼                              │
   KIS Open API (vps→prod)        Telegram Bot API
```

핵심 설계 원칙:
- **vps와 prod의 코드 경로 동일** (`KISAuth.changeTREnv()`만 차이) → 페이퍼 검증 = 라이브 검증
- **신호 생성과 트레이딩 분리** → alpha-hunter 신호 파일 갱신 실패 시 트레이더는 다음 규칙을 따름: 신호 파일 mtime이 직전 영업일 23:00 KST보다 오래되면 **그날 신규 진입 전면 금지**, 기존 포지션은 보유 규칙대로 청산. 부분 갱신·추정·복구 로직 없음.
- **상태는 파일 기반 영속화** → 프로세스 재시작 시 포지션·체결·일일 PnL 복원
- **읽기·쓰기 잠금 분리** → 대시보드는 read-only, 트레이더만 write

## 4. 단계별 일정

### Phase 0: 사전 준비 (D-7 ~ D0, 약 1주)

| # | 작업 | 산출물 | 검증 |
|---|---|---|---|
| 0.1 | KIS 모의투자 계좌 발급 + 앱키 발급 | `~/KIS/config/kis_devlp.yaml` (vps 키) | `python -c "from backtester.kis_auth import auth; auth(svr='vps')"` 토큰 발급 성공 |
| 0.2 | 텔레그램 봇 생성 + chat_id 확보 | `~/.config/composite-trader/secrets.env` (`TG_BOT_TOKEN`, `TG_CHAT_ID`) | 테스트 메시지 1건 수신 |
| 0.3 | WSL Ubuntu에 Python 3.11 + uv/pip + 의존성 설치, repo clone | `wsl: /home/<user>/open-trading-api`, `/home/<user>/alpha-hunter` | `pytest backtester/tests` 통과 |
| 0.4 | systemd-on-WSL 활성화 + Windows 작업 스케줄러 설정 | `wsl --install` 옵션, `wsl.conf [boot] systemd=true` | `systemctl status` 정상 |
| 0.5 | 신호 파일 위치·갱신 주기 설계 결정 | `docs/composite_signal_pipeline.md` | 다음 거래일 신호 1건 생성 성공 |

### Phase 1: 신호 + 트레이더 신규 구현 (D0 ~ D+5, 약 5영업일)

새로 만들 코드:

| 경로 | 역할 | 라인 추정 |
|---|---|---|
| `backtester/live/composite_signal_pipeline.py` | 매일 alpha-hunter 신호를 라이브용 캐시로 재생성 (`reports/kr_research/composite_live_signals_YYYYMMDD.json`) | ~150 |
| `backtester/live/strategy/composite_runner.py` | 5m 봉 마감 → 신호 매칭 → 진입 결정 (BASE_CONFIG, m_bull_20_60 필터) | ~250 |
| `backtester/live/order_manager.py` | 호가 단위 라운딩, 시장가/지정가 분기, 부분체결·재시도(최대 3회) | ~200 |
| `backtester/live/position_tracker.py` | 파일 기반 포지션 영속화 (`~/KIS/state/positions.json`), 손익 누적 | ~150 |
| `backtester/live/risk/killswitch.py` | 보수적 한도 감시 → HALT 플래그 (`~/KIS/state/HALT.flag`) | ~120 |
| `backtester/live/notify/telegram.py` | 메시지 큐 + rate limit + 재시도 | ~100 |
| `backtester/live/heartbeat.py` | 60초 ping, WS 연결 상태, 마지막 5m 봉 수신 시각 | ~80 |
| `backtester/live/trader_loop.py` | 메인 루프 (08:50 시작, 15:35 종료), 시그널 핸들러 | ~250 |
| `backtester/live/dashboard/api.py` | 기존 `backend/`(8002) 위에 라이브 라우터 추가 (read-only) | ~150 |
| `deploy/wsl/composite-trader.service` | systemd unit | - |
| `deploy/wsl/composite-signal.service` + `.timer` | 매일 08:30 신호 갱신 | - |
| `deploy/wsl/composite-heartbeat.service` | 하트비트 | - |

수정될 기존 코드:

| 파일 | 변경 |
|---|---|
| `backtester/kis_auth.py` (라인 146-191 `changeTREnv`) | 환경변수 `COMPOSITE_TRADER_MODE` 우선 적용 분기 1줄 추가 |
| `backtester/kis_backtest/providers/kis/brokerage.py` (라인 56-120 `submit_order`) | 호가단위 라운딩 헬퍼 호출, 부분체결 응답 파싱 강화 |
| `backtester/kis_backtest/providers/kis/websocket.py` | 재연결 백오프(1s→5s→30s), heartbeat 콜백 노출 |
| `backtester/backend/state.py` | 라이브 모드 상태 노출 (대시보드용) |

### Phase 2: 모의투자(vps) 페이퍼 운영 (D+5 ~ D+12, 1주 = 5영업일)

운영 매뉴얼:
- **08:30 KST**: 신호 파이프라인 실행 (`composite-signal.service`) → 오늘의 후보 종목 리스트 생성
- **08:50 KST**: 트레이더 시작 (시장 레짐 `m_bull_20_60` 평가, 진입 후보 잠금)
- **09:00~15:20 KST**: 5m 봉 마감마다 신호 매칭, 신호 발생 시 1m 진입 시그널로 시장가 매수, 손절·익절 모니터
- **15:20 KST**: 신규 진입 차단, 잔여 포지션은 일관 청산 규칙 따름
- **15:35 KST**: 일일 리포트 텔레그램 + 대시보드 갱신

매일 수집할 측정치 (`~/KIS/state/paper_metrics_YYYYMMDD.json`):
- 시뮬레이션 진입가 vs 실측 체결가 차이 (bp)
- 시뮬레이션 청산 시각 vs 실측 시각 (초)
- WS 단절 횟수·총 단절 시간
- API 에러율 (4xx/5xx 비율)
- 메인 루프 평균 처리 지연 (5m 봉 수신 → 주문 발행)

### Phase 3: 게이트 검증 (D+12, 0.5일)

**소프트 게이트 통과 조건** (모두 충족 필요):
1. 5영업일 중 4일 이상 메인 루프가 장중 정상 동작 (의도하지 않은 정지 ≤1시간/일)
2. 시뮬레이션 대비 슬리피지 평균 ±10bp 이내, 최대 ±25bp 이내
3. 발생한 모든 시뮬 진입 신호에 대해 실제 주문이 발행됨 (누락 0건)
4. 부분체결·재시도 로직이 정상 동작 (수동 개입 0회)
5. 텔레그램 알림 도달률 100% (테스트 메시지 포함)
6. WS 단절 발생 시 자동 재연결 성공 (수동 개입 0회)

부족 시: +1주 연장 (Phase 2 반복).

### Phase 4: 라이브(prod) 소액 전환 (D+13 ~ D+27, 약 2주 안정화)

| 단계 | 기간 | 자본 | 비고 |
|---|---|---|---|
| 4a. Carve-out | D+13 ~ D+15 (3영업일) | **100만원** | 모드 전환 후 작은 노출로 실전 체결률 재측정. 페이퍼 측정치와 비교 |
| 4b. Ramp-up | D+16 ~ D+22 (5영업일) | **300만원** | 4a 슬리피지가 페이퍼 ±5bp 이내면 진행 |
| 4c. Target | D+23 ~ | **500만원** | 4b 통과 시 목표 자본 도달 |

전환 절차:
1. 시장 마감 후(15:35 이후) `~/KIS/config/KIS_MODE`를 `prod`로 변경 (TradingState 60초 쿨다운 준수)
2. 토큰 신규 발급 + 잔고 확인
3. `COMPOSITE_TRADER_CAPITAL` 환경변수로 자본 한도 명시 (코드에서 strict 체크)
4. 첫 1시간은 진입 신호 발생 시 텔레그램으로 **수동 승인 요청** 후 진행 (드라이런 모드)
5. 1시간 후 자동 모드 전환

## 5. 모니터링 / 알림 (Observability)

### 5.1 텔레그램 알림 카테고리 (rate-limited)

| 카테고리 | 트리거 | 우선순위 |
|---|---|---|
| `STARTUP` | 트레이더 시작/종료 | INFO |
| `SIGNAL` | 진입 신호 발생 (시뮬 PF·예상가) | INFO |
| `ORDER` | 주문 발행/체결/취소 (체결가·수량·슬리피지) | INFO |
| `EXIT` | 포지션 청산 (P&L, 보유시간) | INFO |
| `WARN` | API 4xx, 호가 단위 미스매치 등 비치명 | WARN |
| `ERROR` | 5xx 반복, WS 단절 1분+, 부분체결 미해결 | ERROR |
| `HALT` | 킬스위치 발동 | CRITICAL (rate-limit 무시) |
| `DAILY` | 15:35 일일 요약 (P&L, 거래수, 승률, vs 시뮬) | INFO |

메시지 포맷 표준화: `[{level}][{strategy}][{ts}] {body}`. 이미지(에쿼티 곡선)는 일일 요약에만 첨부.

### 5.2 로컬 대시보드 (`localhost:8002/composite/live`)

추가 페이지 (read-only, 5초 폴링):
- 현재 모드 (vps/prod) + 자본 한도 + HALT 상태
- 오늘 진입 신호 리스트 + 처리 상태 (대기/주문중/체결/청산)
- 실시간 포지션 + 미실현 P&L
- 일일 누적 P&L 곡선 (분 단위)
- WS 헬스 (마지막 시세 수신 시각, 누적 단절 시간)
- 최근 30일 텔레그램 알림 로그

### 5.3 헬스체크

- 60초마다 `~/KIS/state/heartbeat.json` 갱신 (`{ts, mode, ws_ok, last_5m_bar, halt}`)
- 별도 systemd unit이 5분 이상 갱신 없으면 텔레그램 CRITICAL 발송

## 6. 킬스위치 (보수적)

발동 조건 (OR):
- 일일 실현 P&L < -3% (자본 대비)
- 누적 실현 P&L < -8% (자본 대비, 라이브 시작 이후)
- 연속 3회 손절
- WebSocket 단절 5분 이상 (재연결 실패 누적)
- API 5xx 응답 5분 내 5회 이상

발동 시 동작:
1. **신규 진입 즉시 차단** (`HALT.flag` 생성)
2. 기존 포지션은 **원래 손절·익절 규칙대로 청산** (강제 청산 X — 시장 충격 방지)
3. 텔레그램 CRITICAL + 사유·스냅샷 첨부
4. **재개는 수동만** (`HALT.flag` 삭제 + systemd reload)

## 7. 리스크 및 완화책 (Risks)

| ID | 리스크 | 가능성 | 영향 | 완화책 |
|---|---|---|---|---|
| R1 | 백테스트 PF 2.56이 라이브에서 큰 폭 하락 | 高 | 高 | Phase 4a 100만원 carve-out로 실측 후 ramp-up. PF<1.0이면 즉시 중단 |
| R2 | alpha-hunter 신호 갱신 실패 | 中 | 高 | 신호 파일 mtime이 직전 영업일 23:00 KST 이전이면 **당일 신규 진입 전면 금지**, 기존 포지션만 정상 청산. 텔레그램 ERROR. 추정·재사용 없음 |
| R3 | Windows PC 재부팅·전원 차단 | 中 | 中 | Windows 작업 스케줄러로 부팅 시 WSL+systemd 자동 시작. 무정전 전원장치(UPS) 권장 |
| R4 | KIS API 일시 장애 (5xx) | 中 | 中 | 지수 백오프 재시도(최대 3회). 5분 내 5회 실패 시 킬스위치 |
| R5 | 부분체결 후 잔량 미처리 | 中 | 中 | 5분 폴링으로 잔량 추적, 시장가 재제출(최대 1회), 그래도 미체결이면 취소+WARN |
| R6 | 토큰 만료 (24h) | 高 | 低 | `kis_auth.py`의 자동 갱신 로직 활용 + 매일 08:50 사전 재발급 |
| R7 | WSL2 클럭 드리프트로 봉 시각 오차 | 低 | 中 | WSL 시작 시 `hwclock --hctosys` + 매일 NTP 동기화 |
| R8 | 슬리피지가 0.55% 가정을 초과 | 中 | 高 | 페이퍼·라이브 carve-out에서 실측. 평균 >0.7%면 운영 중단 + 사용자 결정(파라미터 재검증·전략 보정 여부) 후 재개. 자동 주문 유형 변경 없음 |
| R9 | 호가 단위 라운딩 오류로 주문 거부 | 低 | 中 | `KOSPI/KOSDAQ` 호가표 헬퍼 + 단위 테스트 (가격대별 호가단위 8개 케이스) |
| R10 | 텔레그램 API 다운 | 低 | 中 | 텔레그램은 ERROR 1회 콘솔/파일 로그로 기록 후 다음 메시지로 진행 (메시지 큐·재전송 없음). 대시보드(`localhost:8002`)와 파일 로그가 사후 진실. CRITICAL 누락이 우려되면 사용자가 대시보드 확인 |

## 8. 검증 단계 (Acceptance Criteria)

### 8.1 단위 테스트 (Phase 1 완료 시)
- [ ] `composite_runner.py` — 백테스트 결과 1일치(2026-04-29) 재현 (시그널 매칭 100% 일치)
- [ ] `order_manager.py` — 호가 단위 라운딩 (KOSPI/KOSDAQ × 8개 가격대) 100% 통과
- [ ] `risk/killswitch.py` — 4가지 발동 조건 각각 모킹 테스트 통과
- [ ] `position_tracker.py` — 프로세스 재시작 시 상태 복원 (3종 시나리오: 무포지션·진입중·체결완료)

### 8.2 통합 테스트 (Phase 2 시작 전)
- [ ] vps 모드에서 모킹 신호 1건 → 주문 발행 → 체결 → 청산 end-to-end 1회 성공
- [ ] WS 강제 단절 → 자동 재연결 30초 내 성공
- [ ] HALT.flag 강제 생성 → 신규 진입 차단, 기존 포지션 정상 청산

### 8.3 운영 게이트 (Phase 3, 6항목 — 섹션 4 Phase 3 참조)

### 8.4 라이브 게이트 (Phase 4a → 4b 진행 조건)
- [ ] 3영업일 동안 실측 슬리피지 평균이 페이퍼 측정값 ±5bp 이내
- [ ] 신규 라이브 한정 이상 (예: 토큰 갱신 실패) 0건

## 9. 롤백 계획

| 시나리오 | 롤백 트리거 | 절차 |
|---|---|---|
| 페이퍼 실패 | 게이트 미통과 | Phase 1으로 복귀, 원인별 fix |
| 라이브 4a 실패 | PF<1.0 또는 슬리피지>0.7% | `KIS_MODE`를 vps로 복귀, +1주 페이퍼 추가 |
| 킬스위치 발동 | HALT.flag 생성 | 사유 분석 → 코드 fix → 단위 테스트 → 페이퍼 1일 → 재개 |
| 데이터 오염 | 백테스트 PF가 신호 재생성 후 급변 | alpha-hunter 신호 git revert + 재검증 |

## 10. 작업 분해 (Task Breakdown)

ralph/team로 실행할 경우의 권장 분해 (병렬 가능 표시):

| ID | 작업 | 의존성 | 추정 |
|---|---|---|---|
| T1 | 신호 파이프라인 (`composite_signal_pipeline.py`) | 0.1 완료 | 1d |
| T2 | 호가단위·주문 매니저 (`order_manager.py` + 단위 테스트) | 0.3 완료 | 1d |
| T3 | 포지션 트래커 (`position_tracker.py`) | (병렬 T2) | 0.5d |
| T4 | 리스크/킬스위치 (`killswitch.py`) | (병렬 T2) | 0.5d |
| T5 | 텔레그램 클라이언트 (`notify/telegram.py`) | 0.2 완료 | 0.5d |
| T6 | 전략 러너 (`composite_runner.py`) | T1 | 1d |
| T7 | 메인 루프 (`trader_loop.py`) | T2,T3,T4,T5,T6 | 1d |
| T8 | 대시보드 라우터 + 프론트 (`backend/`) | T3 (read-only) | 1d |
| T9 | systemd units + Windows 작업 스케줄러 | T7 | 0.5d |
| T10 | 통합 테스트 (vps end-to-end) | T7 + T9 | 0.5d |

총합: 약 7~8 영업일 (Phase 1).

## 11. 비범위 (Out of Scope)

- V4.1 또는 다른 전략 라이브화 (별도 plan에서 다룸)
- 클라우드 마이그레이션 (필요 시 후속)
- 멀티 종목 동시 보유(max_positions>1) — 현 전략은 1
- 옵션·해외주식 — KOSPI/KOSDAQ 현물만
- 자동 학습/파라미터 재최적화 — 수동만

## 12. 후속 과제 (Follow-ups)

- 라이브 안정 후: V4.1 병행 추가 (best_universe), 자본 50:50 배분
- 슬리피지 학습: 1개월 라이브 데이터로 시간대·종목별 슬리피지 모델 학습 → 진입 예측 보정
- 텔레그램 → Slack 병행 (팀 확장 시)
- 클라우드 이전 (Oracle Free Tier 또는 Seoul AWS) — Windows PC 의존성 제거

---

## 부록 A: 코드 참조 (현 상태)

핵심 파일과 시작 라인:
- 전략 본체: `backtester/kis_backtest/custom/kr_5m_composite_mbull2060.py:39` (params), `:289` (run loop)
- 신호 로더: `alpha-hunter/scripts/validate_kr_5m_composite_1m_execution.py:64` (`_load_5m_signals`)
- 시장 레짐 필터: `alpha-hunter/scripts/filter_kr_5m_composite_market_regime.py:89` (`m_bull_20_60` 정의)
- KIS 인증: `backtester/kis_auth.py:146` (`changeTREnv`), `:443` (vps TR ID 변환)
- 주문: `backtester/kis_backtest/providers/kis/brokerage.py:56` (`submit_order`)
- WebSocket: `backtester/kis_backtest/providers/kis/websocket.py:1`
- 라이브 예제(참조): `backtester/examples/live_trading.py:34`
- 상태: `backtester/backend/state.py:1` (모드 전환 60초 쿨다운, 토큰 자동 복원)
- 페이퍼 백테스트(현재): `backtester/scripts/validation_v2/paper_run.py:233`

## 부록 B: 환경 변수 / 비밀

| 변수 | 위치 | 용도 |
|---|---|---|
| `COMPOSITE_TRADER_MODE` | `~/.config/composite-trader/secrets.env` | `vps` or `prod` (KIS_MODE 파일보다 우선) |
| `COMPOSITE_TRADER_CAPITAL` | 위와 동일 | 자본 상한 (KRW). 코드에서 strict 체크 |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | 위와 동일 | 텔레그램 |
| `KIS_APPKEY` / `KIS_APPSECRET` (vps/prod 각각) | `~/KIS/config/kis_devlp.yaml` | 기존 인프라 재활용 |

비밀 파일은 모두 `chmod 600`. git ignore. 백업은 외장디스크에 LUKS/zip+pw 1부.

## 부록 C: 일정 요약

```
W-1 (D-7~D0):  Phase 0 사전 준비
W1  (D0~D5):   Phase 1 신규 코드 구현 (병렬 T1~T10)
W2  (D5~D12):  Phase 2 vps 페이퍼 운영 (5영업일)
W2 末 (D12):   Phase 3 게이트 검증 (0.5일)
W3  (D13~D15): Phase 4a 라이브 100만원 (3영업일)
W3~4(D16~D22):Phase 4b 라이브 300만원 (5영업일)
W4+ (D23~):    Phase 4c 라이브 500만원 (목표)
```

낙관 일정 약 4주 (게이트 통과 1회 가정). 게이트 1회 미통과 시 +1주.
