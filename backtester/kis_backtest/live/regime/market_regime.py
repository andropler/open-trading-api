"""시장 레짐 플래그 (m_bull_20_60, m_no_1d_shock, m_no_5d_drawdown).

시장지수(KODEX 200, 069500 권장) 일봉 OHLCV를 받아서 5m Composite 전략의
진입 차단 플래그를 계산한다. asof_date 직전까지 데이터만 사용해 look-ahead
편향을 차단한다.

alpha-hunter 의존성 0 — pandas와 표준 라이브러리만 사용.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class RegimeFlags:
    m_bull_20_60: bool
    m_no_1d_shock: bool
    m_no_5d_drawdown: bool

    def passes_base_gate(self) -> bool:
        return self.m_bull_20_60 and self.m_no_1d_shock and self.m_no_5d_drawdown


def compute_flags(daily: pd.DataFrame, asof_date) -> RegimeFlags:
    """asof_date 의 진입 가능 여부를 판단하는 레짐 플래그 계산.

    daily: 시장지수 일봉. 'date'와 'close' 컬럼 필수.
    asof_date: 평가 기준일 (Timestamp/str). 그 직전까지의 데이터만 사용.

    데이터가 60개 미만이면 SMA60 계산 불가로 모든 플래그 False.
    """
    if "date" not in daily.columns or "close" not in daily.columns:
        raise ValueError("daily must have 'date' and 'close' columns")
    asof = pd.Timestamp(asof_date).normalize()
    df = daily.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)
    df = df[df["date"] < asof]
    if len(df) < 60:
        return RegimeFlags(False, False, False)
    closes = df["close"].astype(float).reset_index(drop=True)
    sma20 = closes.rolling(20).mean().iloc[-1]
    sma60 = closes.rolling(60).mean().iloc[-1]
    last_close = closes.iloc[-1]
    ret5 = closes.iloc[-1] / closes.iloc[-6] - 1.0
    ret1 = closes.iloc[-1] / closes.iloc[-2] - 1.0
    high5 = closes.iloc[-5:].max()
    dd5 = closes.iloc[-1] / high5 - 1.0
    return RegimeFlags(
        m_bull_20_60=bool(last_close > sma20 and sma20 > sma60 and ret5 > 0),
        m_no_1d_shock=bool(ret1 > -0.02),
        m_no_5d_drawdown=bool(dd5 > -0.035),
    )
