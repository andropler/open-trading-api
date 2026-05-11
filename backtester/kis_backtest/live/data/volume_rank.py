"""KIS 거래대금/거래량 순위 API wrapper.

엔드포인트: /uapi/domestic-stock/v1/quotations/volume-rank (TR_ID: FHPST01710000).
매일 아침 장 시작 전(예: 08:00 KST) 호출해 전일 거래대금/거래량 상위 N 종목을
조회 → universe.json 으로 저장 → run_live.py 가 그 파일을 로드해 main loop 가동.

KIS 자격증명/yaml sync 는 호출자(scripts/build_universe.py)가 책임.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

_API_PATH = "/uapi/domestic-stock/v1/quotations/volume-rank"
_TR_ID = "FHPST01710000"

# FID_BLNG_CLS_CODE — 정렬 기준
_RANK_CODE = {
    "volume": "0",  # 평균거래량
    "trading_value": "3",  # 거래금액순
    "volume_growth": "1",  # 거래증가율
    "turnover": "2",  # 평균거래회전율
}

# FID_INPUT_ISCD — 시장 코드
_MARKET_CODE = {
    "ALL": "0000",
    "KOSPI": "0001",
    "KOSDAQ": "1001",
}

Market = Literal["ALL", "KOSPI", "KOSDAQ"]
RankBy = Literal["volume", "trading_value", "volume_growth", "turnover"]


class _ResponseLike(Protocol):
    def is_ok(self) -> bool: ...
    def get_output(self, key: str = "output") -> list[dict]: ...
    def getErrorMessage(self) -> str: ...


class _AuthLike(Protocol):
    def get(self, path: str, params: dict, tr_id: str) -> _ResponseLike: ...


@dataclass(frozen=True)
class RankingEntry:
    ticker: str
    name: str
    price: int
    volume: int
    trading_value: int  # KRW
    rank: int  # 1-based


def fetch_volume_rank(
    auth: _AuthLike,
    *,
    market: Market = "ALL",
    rank_by: RankBy = "trading_value",
    top_n: int = 30,
    min_price: int = 5_000,
    exclude_etf: bool = True,
) -> list[RankingEntry]:
    """KIS volume-rank 호출 → 상위 N개 종목.

    KIS 응답은 보통 30개까지 반환. top_n>30 이면 30 까지만.
    """
    if market not in _MARKET_CODE:
        raise ValueError(f"market must be ALL/KOSPI/KOSDAQ, got {market!r}")
    if rank_by not in _RANK_CODE:
        raise ValueError(
            f"rank_by must be {list(_RANK_CODE.keys())}, got {rank_by!r}"
        )
    if top_n <= 0:
        raise ValueError(f"top_n must be >= 1, got {top_n}")

    # FID_DIV_CLS_CODE: 1=보통주(ETF 제외 효과), 2=우선주, 0=전체
    div_code = "1" if exclude_etf else "0"

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": _MARKET_CODE[market],
        "FID_DIV_CLS_CODE": div_code,
        "FID_BLNG_CLS_CODE": _RANK_CODE[rank_by],
        # 시가총액 전체 (각 자리 0=제외 1=포함, 9자리)
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000",
        "FID_INPUT_PRICE_1": str(min_price),
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }
    resp = auth.get(_API_PATH, params, _TR_ID)
    if not resp.is_ok():
        raise RuntimeError(
            f"KIS volume-rank failed: {resp.getErrorMessage()}"
        )
    rows = resp.get_output("output") or []

    entries: list[RankingEntry] = []
    for i, row in enumerate(rows[:top_n], start=1):
        ticker = str(row.get("mksc_shrn_iscd", "")).strip()
        if not ticker:
            continue
        try:
            entries.append(
                RankingEntry(
                    ticker=ticker,
                    name=str(row.get("hts_kor_isnm", "")).strip(),
                    price=int(row.get("stck_prpr", 0) or 0),
                    volume=int(row.get("acml_vol", 0) or 0),
                    trading_value=int(row.get("acml_tr_pbmn", 0) or 0),
                    rank=i,
                )
            )
        except (TypeError, ValueError) as e:
            logger.warning("skip malformed row %s: %s", row, e)
    return entries


__all__ = ["RankingEntry", "fetch_volume_rank"]
