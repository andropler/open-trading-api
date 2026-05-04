# KR 5m Composite m_bull_20_60 Strategy

이 문서는 `alpha-hunter`에서 연구한 최종 국내주식 5분봉 합성 전략을 `open-trading-api` 저장소에 남기는 고정 스펙이다.

## 최종 결론

최종 채택 전략은 `pf_target_tighter_slots1 + m_bull_20_60`이다.

- 신호 타임프레임: 5분봉
- 체결 검증 타임프레임: 1분봉
- 동시 보유 포지션: 1개
- 시장 필터: 전일 `069500` 기준 `close > SMA20`, `SMA20 > SMA60`, `5일 수익률 > 0`

## 시장상태 필터

진입일 D에는 D-1까지의 `069500` 데이터만 사용한다.

```text
market_ok =
    prev_close > prev_sma20
    and prev_sma20 > prev_sma60
    and prev_5d_return > 0
```

## 진입 패밀리

하나의 전략 안에서 세 가지 진입 로직을 합성한다.

### 1. Reclaim Strict

급등 이후 장중 VWAP reclaim을 노린다.

- 후보: 최근 3거래일 수익률 25%~50%
- 갭: 0%~8%
- reclaim 종료: 10:30
- 거래량 확인: 80개 5분봉 평균 대비 2배 이상
- 허용 진입 시간: `09:35`, `09:40`, `09:45`, `09:55`, `10:00`, `10:05`, `10:10`, `10:15`, `10:25`

청산:

- 손절: 3%
- 익절: 10%
- 트레일링: +5% 도달 후 4% trail
- 시간청산: 14:30

### 2. ORB Event Quality

전일 이벤트성 거래대금/거래량 이후 opening range breakout을 노린다.

- 후보: event mode
- 전일 거래대금: 100억 이상
- 전일 거래량: 20일 평균 대비 1.5배 이상
- 최소 가격: 5,000원
- 갭: 0%~3%
- opening range: 09:00~09:30
- 허용 진입 시간: 10:00~10:20

청산:

- 손절: 3%
- 익절: 8%
- 트레일링: +4% 도달 후 3% trail
- 시간청산: 14:30

### 3. Native Close Top15

5분봉 자체 유동성/모멘텀 상위 종목의 돌파를 노린다.

- breakout lookback: 12개 5분봉
- breakout 기준: close breakout
- 거래량 기준: same-time 평균 대비 2배
- 일별 유동성 랭킹: 상위 15개
- 최소 가격: 5,000원
- 허용 진입 시간: 10:25

청산:

- 손절: 5%
- 익절: 10%
- 트레일링: +2% 도달 후 1.5% trail
- 최대 보유: 익일

## 성과

연구 기간: `2025-04-25` ~ `2026-04-29`

1분봉 체결 검증 기준:

```text
Signals: 89
Trades: 69
Total return: 161.8%
PF@0.55: 2.560
PF@1.00: 1.922
MDD: -13.4%
Reclaim / ORB / Native trades: 26 / 29 / 14
```

비용 스트레스:

```text
Cost 0.55%: PF 2.560 / Total 161.8% / MDD -13.4%
Cost 1.00%: PF 1.922 / Total 110.6% / MDD -16.6%
Cost 1.25%: PF 1.640 / Total 82.2%  / MDD -20.1%
Cost 1.50%: PF 1.398 / Total 53.8%  / MDD -27.3%
Cost 2.00%: PF 1.025 / Total -3.0%  / MDD -50.5%
```

## 폐기한 후보

아래 후보는 거래수는 늘렸지만 PF 또는 비용 내성이 약해 최종 전략에서 제외했다.

- `reclaim_loose`
- `orb_recent_fast`
- `native_high_scalp`
- 시장 필터 없는 baseline 합성 전략
- 비용 1.25% 이상에서 무너지는 no-regime 후보

## 운용 판단

고정 `m_bull_20_60` 룰의 인샘플 성과는 강하다. 다만 월별 walk-forward에서는 아래 수준으로 낮아졌다.

```text
Trades: 42
Total return: 43.1%
PF@0.55: 1.880
PF@1.00: 1.435
MDD: -11.0%
```

따라서 실거래 투입 전 최소 1~3개월 forward-test가 필요하다.
