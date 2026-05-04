"""로컬 parquet 파일 기반 DataProvider.

`/Users/benjamin/personal_workspace/shared_data/kr_stocks/{daily,5m,1h}` 의
parquet 데이터를 읽어 `DataProvider` Protocol을 구현한다.

파일 명명 규칙(자동 인식):
    daily/{symbol}.parquet
    daily/{symbol}.KS_1d.parquet      (ETF 등 일부 종목)
    5m/{symbol}_5m.parquet
    1h/{symbol}_1h.parquet

KOSPI 벤치마크는 KODEX 200(069500) ETF를 대용으로 사용한다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Callable, List, Optional

import pandas as pd

from ...models import Bar, Quote, Resolution, IndexBar
from ...models.market_data import StockInfo, FinancialData
from ...models.trading import Subscription

logger = logging.getLogger(__name__)


DEFAULT_DATA_ROOT = Path("/Users/benjamin/personal_workspace/shared_data/kr_stocks")

KOSPI_BENCHMARK_SYMBOL = "069500"  # KODEX 200 ETF (KOSPI 벤치마크 대용)


class ParquetDataProvider:
    """로컬 parquet 파일에서 OHLCV를 읽어 Bar 리스트로 반환.

    Lean의 _fetch_data가 호출하는 get_history만 정상 구현하고,
    get_quote / subscribe_realtime 같은 라이브 메서드는 백테스트에 필요 없으므로 stub 처리.
    """

    def __init__(self, data_root: Optional[Path] = None) -> None:
        self.data_root = Path(data_root) if data_root else DEFAULT_DATA_ROOT
        if not self.data_root.exists():
            raise FileNotFoundError(f"parquet data root가 없습니다: {self.data_root}")
        self._dir_for_resolution = {
            Resolution.DAILY: self.data_root / "daily",
            Resolution.MINUTE: self.data_root / "5m",
            Resolution.HOUR: self.data_root / "1h",
        }
        self._cache: dict[tuple[str, Resolution], pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # 파일 경로 해석
    # ------------------------------------------------------------------

    def _resolve_path(self, symbol: str, resolution: Resolution) -> Optional[Path]:
        directory = self._dir_for_resolution.get(resolution)
        if directory is None or not directory.exists():
            return None

        if resolution == Resolution.DAILY:
            candidates = [
                directory / f"{symbol}.parquet",
                directory / f"{symbol}.KS_1d.parquet",
                directory / f"{symbol}.KQ_1d.parquet",
            ]
        elif resolution == Resolution.MINUTE:
            candidates = [directory / f"{symbol}_5m.parquet"]
        elif resolution == Resolution.HOUR:
            candidates = [directory / f"{symbol}_1h.parquet"]
        else:
            return None

        for path in candidates:
            if path.exists():
                return path
        return None

    def _load(self, symbol: str, resolution: Resolution) -> Optional[pd.DataFrame]:
        key = (symbol, resolution)
        if key in self._cache:
            return self._cache[key]

        path = self._resolve_path(symbol, resolution)
        if path is None:
            return None

        df = pd.read_parquet(path)
        if "timestamp" not in df.columns:
            logger.warning("timestamp 컬럼 없음: %s", path)
            return None

        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        self._cache[key] = df
        return df

    # ------------------------------------------------------------------
    # DataProvider Protocol 메서드
    # ------------------------------------------------------------------

    def get_history(
        self,
        symbol: str,
        start: date,
        end: date,
        resolution: Resolution = Resolution.DAILY,
    ) -> List[Bar]:
        df = self._load(symbol, resolution)
        if df is None or df.empty:
            return []

        start_dt = pd.Timestamp(start)
        end_dt = pd.Timestamp(end) + pd.Timedelta(days=1)  # end inclusive
        mask = (df["timestamp"] >= start_dt) & (df["timestamp"] < end_dt)
        sliced = df.loc[mask]
        if sliced.empty:
            return []

        bars: List[Bar] = []
        for row in sliced.itertuples(index=False):
            try:
                bars.append(
                    Bar(
                        time=row.timestamp.to_pydatetime() if hasattr(row.timestamp, "to_pydatetime") else row.timestamp,
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=int(row.volume) if not pd.isna(row.volume) else 0,
                    )
                )
            except Exception as e:  # 결측/이상치 행은 건너뜀
                logger.debug("bar 변환 실패 %s @ %s: %s", symbol, getattr(row, "timestamp", "?"), e)
                continue
        return bars

    def get_index_history(
        self,
        index_code: str,
        start: date,
        end: date,
    ) -> List[IndexBar]:
        """KOSPI(0001) 벤치마크 — KODEX 200 ETF로 대용."""
        if index_code not in {"0001", "KOSPI", "kospi"}:
            return []

        bars = self.get_history(KOSPI_BENCHMARK_SYMBOL, start, end, Resolution.DAILY)
        return [
            IndexBar(
                time=b.time,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
            for b in bars
        ]

    def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError("ParquetDataProvider는 라이브 호가를 지원하지 않습니다.")

    def subscribe_realtime(
        self,
        symbols: List[str],
        on_bar: Callable[[str, Bar], None],
    ) -> Subscription:
        raise NotImplementedError("ParquetDataProvider는 실시간 구독을 지원하지 않습니다.")

    def get_stock_info(self, symbol: str) -> Optional[StockInfo]:
        return None

    def get_financial_data(self, symbol: str) -> Optional[FinancialData]:
        return None

    # ------------------------------------------------------------------
    # 부가 유틸: sweep 인프라에서 사용
    # ------------------------------------------------------------------

    def list_symbols(self, resolution: Resolution = Resolution.DAILY) -> List[str]:
        """해당 해상도에서 사용 가능한 종목 코드 목록."""
        directory = self._dir_for_resolution.get(resolution)
        if directory is None or not directory.exists():
            return []

        symbols: set[str] = set()
        for path in directory.glob("*.parquet"):
            stem = path.stem
            if resolution == Resolution.DAILY:
                # "069500.KS_1d" -> "069500" / "005930" -> "005930"
                symbol = stem.split(".", 1)[0]
            elif resolution == Resolution.MINUTE:
                symbol = stem.removesuffix("_5m")
            elif resolution == Resolution.HOUR:
                symbol = stem.removesuffix("_1h")
            else:
                continue
            if symbol:
                symbols.add(symbol)
        return sorted(symbols)

    def has_symbol(self, symbol: str, resolution: Resolution = Resolution.DAILY) -> bool:
        return self._resolve_path(symbol, resolution) is not None
