"""티커별 일봉 OHLCV parquet 캐시.

쓰기는 tmp 파일 + os.replace 로 atomic. 손상된 파일은 .corrupt-{ts}.parquet 로
백업하고 RuntimeError 발생.
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import date as _date
from pathlib import Path
from typing import Optional

import pandas as pd

REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "volume")


class DailyOHLCVCache:
    def __init__(self, root_dir: Path | str):
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def path(self, symbol: str) -> Path:
        return self.root_dir / f"{symbol}_daily.parquet"

    def read(self, symbol: str) -> Optional[pd.DataFrame]:
        p = self.path(symbol)
        if not p.exists():
            return None
        try:
            df = pd.read_parquet(p)
        except Exception as e:
            backup = p.with_name(f"{p.stem}.corrupt-{int(time.time())}{p.suffix}")
            shutil.move(str(p), str(backup))
            raise RuntimeError(
                f"daily cache parquet corrupted at {p}, backed up to {backup}: {e}"
            ) from e
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        return df.sort_values("date").reset_index(drop=True)

    def write(self, symbol: str, df: pd.DataFrame) -> None:
        if df.empty:
            raise ValueError(f"refusing to write empty DataFrame for {symbol}")
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"DataFrame missing columns: {missing}")
        out = df.loc[:, list(REQUIRED_COLUMNS)].copy()
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
        out = out.sort_values("date").reset_index(drop=True)
        target = self.path(symbol)
        tmp = target.with_suffix(target.suffix + ".tmp")
        out.to_parquet(tmp, index=False)
        os.replace(str(tmp), str(target))

    def last_date(self, symbol: str) -> Optional[_date]:
        df = self.read(symbol)
        if df is None or df.empty:
            return None
        return pd.Timestamp(df["date"].iloc[-1]).date()
