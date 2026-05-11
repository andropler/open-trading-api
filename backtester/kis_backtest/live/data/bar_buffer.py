"""FiveMinuteBarBuffer: in-memory 5m 봉 누적 + parquet snapshot.

라이브 봇은 09:00~15:30 동안 KIS WebSocket 으로 5m 봉을 받아 이 버퍼에 append.
신호 엔진은 get(symbol) 으로 정렬된 DataFrame 을 조회. 장 마감 후 snapshot
호출로 디버깅·리플레이용 parquet 저장. clear() 로 다음 거래일 메모리 정리.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Optional

import pandas as pd

REQUIRED_BAR_KEYS = ("time", "open", "high", "low", "close", "volume")
_COLUMNS = list(REQUIRED_BAR_KEYS)


@dataclass
class FiveMinuteBarBuffer:
    snapshot_dir: Optional[Path] = None
    _bars: dict[str, list[dict]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.snapshot_dir is not None:
            self.snapshot_dir = Path(self.snapshot_dir)
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def append(self, symbol: str, bar: dict) -> None:
        missing = [k for k in REQUIRED_BAR_KEYS if k not in bar]
        if missing:
            raise ValueError(f"bar dict missing keys: {missing}")
        self._bars.setdefault(symbol, []).append(dict(bar))

    def get(self, symbol: str) -> pd.DataFrame:
        rows = self._bars.get(symbol, [])
        if not rows:
            return pd.DataFrame(columns=_COLUMNS)
        df = pd.DataFrame(rows)
        return df.sort_values("time").reset_index(drop=True)

    def snapshot(self, asof_date: _date) -> Optional[Path]:
        """5m 봉을 asof_date 디렉토리 아래에 심볼별 parquet 으로 dump.

        snapshot_dir 미설정이면 no-op. 빈 버퍼라도 date 디렉토리는 생성되어
        "당일 snapshot 시도 기록" 으로 활용 가능하다.
        """
        if self.snapshot_dir is None:
            return None
        date_dir = self.snapshot_dir / asof_date.isoformat()
        date_dir.mkdir(parents=True, exist_ok=True)
        for symbol, rows in self._bars.items():
            if not rows:
                continue
            df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
            target = date_dir / f"{symbol}_5m.parquet"
            tmp = target.with_suffix(target.suffix + ".tmp")
            df.to_parquet(tmp, index=False)
            os.replace(str(tmp), str(target))
        return date_dir

    def clear(self) -> None:
        self._bars.clear()

    def symbols(self) -> list[str]:
        return list(self._bars.keys())
