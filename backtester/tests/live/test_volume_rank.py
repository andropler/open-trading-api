from __future__ import annotations

import pytest

from kis_backtest.live.data.volume_rank import RankingEntry, fetch_volume_rank


class FakeResponse:
    def __init__(self, ok: bool = True, output: list[dict] | None = None, error: str = ""):
        self._ok = ok
        self._output = output or []
        self._error = error

    def is_ok(self) -> bool:
        return self._ok

    def get_output(self, key: str = "output") -> list[dict]:
        return self._output

    def getErrorMessage(self) -> str:
        return self._error


class FakeAuth:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[tuple[str, dict, str]] = []

    def get(self, path: str, params: dict, tr_id: str) -> FakeResponse:
        self.calls.append((path, params, tr_id))
        return self.response


def _row(ticker, name, price, volume, value):
    return {
        "mksc_shrn_iscd": ticker,
        "hts_kor_isnm": name,
        "stck_prpr": str(price),
        "acml_vol": str(volume),
        "acml_tr_pbmn": str(value),
    }


class TestBasic:
    def test_returns_entries_ordered(self):
        rows = [
            _row("005930", "삼성전자", 70000, 10_000_000, 700_000_000_000),
            _row("000660", "SK하이닉스", 100000, 5_000_000, 500_000_000_000),
        ]
        auth = FakeAuth(FakeResponse(output=rows))
        entries = fetch_volume_rank(auth, top_n=10)
        assert len(entries) == 2
        assert entries[0] == RankingEntry(
            ticker="005930",
            name="삼성전자",
            price=70000,
            volume=10_000_000,
            trading_value=700_000_000_000,
            rank=1,
        )
        assert entries[1].rank == 2

    def test_top_n_limits_output(self):
        rows = [_row(f"00{i:04d}", f"종목{i}", 10000, 1000, 10_000) for i in range(20)]
        auth = FakeAuth(FakeResponse(output=rows))
        entries = fetch_volume_rank(auth, top_n=5)
        assert len(entries) == 5

    def test_api_params_use_trading_value_rank(self):
        auth = FakeAuth(FakeResponse(output=[]))
        fetch_volume_rank(auth, rank_by="trading_value", market="KOSPI")
        path, params, tr_id = auth.calls[0]
        assert "volume-rank" in path
        assert params["FID_BLNG_CLS_CODE"] == "3"  # trading_value
        assert params["FID_INPUT_ISCD"] == "0001"  # KOSPI
        assert params["FID_DIV_CLS_CODE"] == "1"  # 보통주(ETF 제외)
        assert tr_id == "FHPST01710000"

    def test_include_etf_changes_div_code(self):
        auth = FakeAuth(FakeResponse(output=[]))
        fetch_volume_rank(auth, exclude_etf=False)
        _, params, _ = auth.calls[0]
        assert params["FID_DIV_CLS_CODE"] == "0"

    def test_min_price_passed(self):
        auth = FakeAuth(FakeResponse(output=[]))
        fetch_volume_rank(auth, min_price=10_000)
        _, params, _ = auth.calls[0]
        assert params["FID_INPUT_PRICE_1"] == "10000"


class TestValidation:
    def test_invalid_market(self):
        auth = FakeAuth(FakeResponse(output=[]))
        with pytest.raises(ValueError, match="market"):
            fetch_volume_rank(auth, market="NYSE")  # type: ignore[arg-type]

    def test_invalid_rank_by(self):
        auth = FakeAuth(FakeResponse(output=[]))
        with pytest.raises(ValueError, match="rank_by"):
            fetch_volume_rank(auth, rank_by="momentum")  # type: ignore[arg-type]

    def test_zero_top_n(self):
        auth = FakeAuth(FakeResponse(output=[]))
        with pytest.raises(ValueError, match="top_n"):
            fetch_volume_rank(auth, top_n=0)


class TestErrorPropagation:
    def test_not_ok_raises_runtime(self):
        auth = FakeAuth(FakeResponse(ok=False, error="rate limit"))
        with pytest.raises(RuntimeError, match="rate limit"):
            fetch_volume_rank(auth)


class TestMalformedRows:
    def test_empty_ticker_skipped(self):
        rows = [
            _row("", "이상", 100, 1, 1),
            _row("005930", "정상", 100, 1, 1),
        ]
        auth = FakeAuth(FakeResponse(output=rows))
        entries = fetch_volume_rank(auth)
        assert len(entries) == 1
        assert entries[0].ticker == "005930"

    def test_missing_numeric_field_defaults_zero(self):
        # KIS 응답에서 빈 문자열이 올 수도 — 0 으로 fallback
        rows = [
            {
                "mksc_shrn_iscd": "005930",
                "hts_kor_isnm": "삼성전자",
                "stck_prpr": "",
                "acml_vol": "",
                "acml_tr_pbmn": "",
            }
        ]
        auth = FakeAuth(FakeResponse(output=rows))
        entries = fetch_volume_rank(auth)
        assert entries[0].price == 0
        assert entries[0].volume == 0
        assert entries[0].trading_value == 0
