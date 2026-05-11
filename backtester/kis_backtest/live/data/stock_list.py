"""shared_data/kr_stocks/_stock_list.parquet 로더 — 보통주 종목코드 집합.

universe 빌더가 KIS volume-rank 결과에서 ETF 등 비-보통주를 제외하기 위한
화이트리스트. parquet 컬럼: code, name, market (KOSPI/KOSDAQ).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_stock_universe(parquet_path: Path) -> set[str]:
    """parquet 의 code 컬럼을 set 으로 반환. 6자리로 0-pad."""
    if not parquet_path.exists():
        raise FileNotFoundError(f"stock list parquet not found: {parquet_path}")
    df = pd.read_parquet(parquet_path)
    if "code" not in df.columns:
        raise ValueError(f"'code' column missing in {parquet_path}")
    return {str(c).strip().zfill(6) for c in df["code"] if str(c).strip()}
