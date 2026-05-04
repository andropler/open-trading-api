"""유동성 기반 유니버스 선정 유틸.

전체 parquet 데이터셋을 훑어 평균 거래대금 (close × volume) 상위 N개 종목을 추출.
백테스트 sweep에서 작은 종목을 제외해 통계 안정성과 실전 가능성을 확보하기 위한 용도.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from ..models import Resolution
from ..providers.parquet import ParquetDataProvider

logger = logging.getLogger(__name__)


def rank_by_turnover(
    provider: ParquetDataProvider,
    resolution: Resolution = Resolution.DAILY,
    lookback_start: Optional[date] = None,
    lookback_end: Optional[date] = None,
    min_bars: int = 100,
) -> pd.DataFrame:
    """모든 종목의 lookback 구간 내 평균 거래대금을 계산해 DataFrame 반환.

    Args:
        provider: ParquetDataProvider 인스턴스
        resolution: 분석 해상도 (일봉/5분봉)
        lookback_start: 평가 시작일 (None이면 데이터 전체)
        lookback_end: 평가 종료일 (None이면 데이터 전체)
        min_bars: 이 미만의 봉 수만 가진 종목은 제외

    Returns:
        DataFrame: ['symbol', 'avg_turnover', 'bars'] (avg_turnover 내림차순)
    """
    symbols = provider.list_symbols(resolution)
    rows: List[dict] = []

    for symbol in symbols:
        df = provider._load(symbol, resolution)  # 캐시 활용
        if df is None or df.empty:
            continue

        if lookback_start is not None:
            df = df[df["timestamp"] >= pd.Timestamp(lookback_start)]
        if lookback_end is not None:
            df = df[df["timestamp"] <= pd.Timestamp(lookback_end)]

        if len(df) < min_bars:
            continue

        try:
            turnover = (df["close"].astype(float) * df["volume"].astype(float)).mean()
        except Exception:
            continue

        if not pd.notna(turnover):
            continue

        rows.append({"symbol": symbol, "avg_turnover": float(turnover), "bars": len(df)})

    ranked = pd.DataFrame(rows).sort_values("avg_turnover", ascending=False).reset_index(drop=True)
    return ranked


def top_n_by_turnover(
    provider: ParquetDataProvider,
    n: int,
    resolution: Resolution = Resolution.DAILY,
    lookback_start: Optional[date] = None,
    lookback_end: Optional[date] = None,
    min_bars: int = 100,
    exclude_etfs: bool = True,
) -> List[str]:
    """유동성 상위 N개 종목 코드 리스트.

    exclude_etfs=True 인 경우 KS_1d 형식 파일 (ETF/리츠 등)을 제외.
    실전 전략 평가에는 일반 주식만 쓰는 게 깨끗하지만, 벤치마크는 별도이므로 sweep 입력에서만 제거.
    """
    ranked = rank_by_turnover(provider, resolution, lookback_start, lookback_end, min_bars)

    if exclude_etfs:
        # ETF/리츠 등은 daily/{symbol}.KS_1d.parquet로 저장됨.
        # has_symbol 만으론 구별이 안되므로 직접 경로 확인.
        directory = provider.data_root / "daily"
        ranked = ranked[
            ranked["symbol"].apply(
                lambda s: (directory / f"{s}.parquet").exists()
            )
        ].reset_index(drop=True)

    return ranked["symbol"].head(n).tolist()


def save_universe(symbols: List[str], path: Path, label: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(symbols)
    if label:
        text = f"# {label}\n# n={len(symbols)}\n{text}\n"
    path.write_text(text)
    logger.info("universe saved: %s (%d symbols)", path, len(symbols))


def load_universe(path: Path) -> List[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out
