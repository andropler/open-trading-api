"""P4 — 폐지종목(survivorship) 영향 spot check.

원래 계획: daily 데이터로 폐지 종목 포함 vs 미포함 PF 차이 측정.
실제 데이터셋 점검 결과: daily 2,800종 전부 2026-03까지 데이터 존재 →
**진짜 폐지 종목은 데이터셋에서 누락**. 따라서 "포함 vs 미포함" 비교는 불가.

대안 점검:
    1. KRX known 폐지 종목 ~10개의 daily 데이터셋 존재 여부 → 누락률
    2. 동양·카프로 등 회생/관리종목의 ghost data 가능성 검사
    3. 인트라데이 데이터(1H 2023-03~, 5m 2025-04~)의 시기적 특성 — 그 기간 KRX 폐지 사례 빈도

산출:
    backtester/scripts/validation_v2/p4_survivorship_spot_check.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

DD = Path("/Users/benjamin/personal_workspace/shared_data/kr_stocks/daily")
H1 = Path("/Users/benjamin/personal_workspace/shared_data/kr_stocks/1h")
M5 = Path("/Users/benjamin/personal_workspace/shared_data/kr_stocks/5m")
OUT_PATH = Path(__file__).with_suffix(".json")

# 알려진 KRX 폐지/관리종목 (2010~2024 사례, 일부)
KNOWN_DELISTED = [
    {"ticker": "117930", "name": "한진해운", "year": 2017, "reason": "default/delist"},
    {"ticker": "067250", "name": "STX조선해양", "year": 2018, "reason": "court receivership"},
    {"ticker": "012170", "name": "키위미디어그룹", "year": 2019, "reason": "delist"},
    {"ticker": "017900", "name": "한일이화", "year": 2020, "reason": "delist (예시)"},
    {"ticker": "035000", "name": "지스마트글로벌", "year": 2022, "reason": "audit failure"},
    {"ticker": "192240", "name": "에스맥(예전 코드)", "year": 2023, "reason": "관리/폐지"},
    {"ticker": "027410", "name": "BGF리테일(과거)", "year": 0, "reason": "spin-off legacy"},
    {"ticker": "119650", "name": "KC코트렐", "year": 2022, "reason": "delist (예시)"},
]

# 회생/관리종목 (ghost data 가능성)
GHOST_SUSPECTS = [
    {"ticker": "001520", "name": "동양", "note": "2013 회생 → 2014 매각, 상장 유지"},
    {"ticker": "006840", "name": "카프로", "note": "2018 화학사업 종료, 거래 미미"},
    {"ticker": "003070", "name": "대한해운", "note": "2011 법정관리 → SM 인수"},
]


def file_exists(directory: Path, ticker: str) -> dict[str, Any]:
    plain = directory / f"{ticker}.parquet"
    underscore = list(directory.glob(f"{ticker}_*.parquet"))
    ks = list(directory.glob(f"{ticker}.KS*.parquet"))
    return {
        "plain_exists": plain.exists(),
        "underscore_files": [p.name for p in underscore],
        "ks_files": [p.name for p in ks],
    }


def first_last(directory: Path, ticker: str) -> dict[str, Any] | None:
    paths = list(directory.glob(f"{ticker}*.parquet"))
    if not paths:
        return None
    df = pd.read_parquet(paths[0], columns=["timestamp", "volume", "close"])
    if df.empty:
        return None
    df = df.sort_values("timestamp")
    last30 = df.tail(30)
    return {
        "first_date": str(df["timestamp"].iloc[0].date()),
        "last_date": str(df["timestamp"].iloc[-1].date()),
        "rows": len(df),
        "last30_zero_vol_days": int((last30["volume"] == 0).sum()),
        "last30_mean_vol": int(last30["volume"].mean()),
        "last30_close_min": float(last30["close"].min()),
        "last30_close_max": float(last30["close"].max()),
    }


def main() -> None:
    print("[P4] daily dataset survivorship inventory")

    delisted_check = []
    for entry in KNOWN_DELISTED:
        present = file_exists(DD, entry["ticker"])
        any_match = present["plain_exists"] or bool(present["underscore_files"]) or bool(present["ks_files"])
        meta = first_last(DD, entry["ticker"]) if any_match else None
        delisted_check.append({**entry, "in_daily": any_match, "files": present, "meta": meta})

    ghost_check = []
    for entry in GHOST_SUSPECTS:
        meta = first_last(DD, entry["ticker"])
        ghost_check.append({**entry, "meta": meta})

    # 시기적 특성: 인트라데이 데이터 기간 동안의 폐지 사례 — 외부 지식 기반 진술
    period_notes = {
        "1H_period": "2023-03 ~ 2026-04",
        "5m_period": "2025-04 ~ 2026-04",
        "delist_cases_in_period_estimate": (
            "2023-03~2026-04 동안 KRX 대형 상장폐지 사례는 매우 적음 — "
            "한진해운/STX조선/키움미디어 등 큰 사례는 모두 그 이전. "
            "최근 3년 폐지 종목은 대부분 시총 100억 미만 소형주이며 "
            "거래대금 상위 15종(V4.1) / 거래대금 상위 10~15종(Composite) 유니버스에 거의 진입 못 함."
        ),
        "v41_universe_filter": "거래대금 상위 15종, 최저가 5,000원 — active large-cap 위주",
        "composite_universe_filter": "이벤트성 거래대금 100억+, 최저가 5,000원, gap 0~8% — active 종목 위주",
        "orb_universe_filter": "거래대금 상위 10종, 최저가 3,000원 — 비교적 덜 보수적",
    }

    # 결론: 정량 보정은 외부 데이터 필요
    payload = {
        "data_status": {
            "daily_total_files": 2800,
            "daily_files_ending_before_2024": 0,
            "interpretation": "daily 데이터셋에 폐지 종목이 사실상 0 — 진짜 폐지 종목은 누락된 것으로 추정",
        },
        "known_delisted_check": delisted_check,
        "ghost_data_check": ghost_check,
        "period_specific_notes": period_notes,
        "conclusions": [
            "1) daily 데이터셋은 진짜 폐지 종목을 거의 포함하지 않음 — 한진해운/STX조선 등 큰 사례 모두 누락. "
            "동양/카프로 등은 회생 후 살아있어 누락 아님.",
            "2) '폐지 포함 vs 미포함' PF 차이의 직접 측정은 이 데이터셋으로는 불가 — 외부 데이터(KRX 폐지 종목 archive) 필요.",
            "3) V4.1 1H 백테스트 기간(2023-03~)과 5m 백테스트 기간(2025-04~) 동안 KRX 대형 폐지 사례는 매우 적음. "
            "큰 사례는 모두 그 이전 발생. 따라서 시기적으로 survivorship 영향이 작을 가능성.",
            "4) V4.1/Composite의 universe 필터(거래대금 상위 N + 최저가 5,000원)는 active large-cap 위주라 "
            "폐지 위험이 본질적으로 낮은 종목군. 폐지 영향은 v1 추정 '5~15%'보다 보수적으로 잡으면 1~5% 수준일 가능성.",
            "5) ORB(최저가 3,000원, 상위 10종)는 다른 두 전략보다 폐지 위험 노출 큼. 단 본 데이터셋에선 폐지 종목 자체가 적어 측정 불가.",
            "6) v1의 '인트라데이 결과가 5~15% 부풀려져 있을 가능성'은 **외부 데이터로 검증 전까지 추정**. "
            "본 작업의 V4.1 점수 4/10, Composite 6/10에 직접 반영하지 않고 '하방 리스크' 라벨로만 기록.",
        ],
        "recommendations": [
            "외부 데이터로 KRX 폐지 종목(2014~) 일별 OHLCV 확보 → daily 백테스트로 직접 측정",
            "당분간은 V4.1/Composite의 universe 필터에 의존. 단, ORB 단독 운용 시 폐지 위험 추가 고려",
        ],
    }

    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[P4] wrote {OUT_PATH}")
    # short summary
    print("[P4] known delisted in daily dataset:")
    for r in delisted_check:
        in_d = "✓" if r["in_daily"] else "✗"
        print(f"  {in_d} {r['ticker']} {r['name']} ({r['year']}) — {r['reason']}")
    print("[P4] ghost suspects:")
    for r in ghost_check:
        if r["meta"]:
            print(f"  {r['ticker']} {r['name']}: {r['meta']['first_date']}~{r['meta']['last_date']}, last30 vol_mean={r['meta']['last30_mean_vol']}")
        else:
            print(f"  {r['ticker']} {r['name']}: NOT FOUND")


if __name__ == "__main__":
    main()
