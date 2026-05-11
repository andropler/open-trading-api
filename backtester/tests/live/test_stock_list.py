from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from kis_backtest.live.data.stock_list import load_stock_universe


def _write_parquet(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    df.to_parquet(path, index=False)


class TestLoad:
    def test_basic_set(self, tmp_path: Path):
        p = tmp_path / "list.parquet"
        _write_parquet(
            p,
            [
                {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
                {"code": "000660", "name": "SK하이닉스", "market": "KOSPI"},
            ],
        )
        universe = load_stock_universe(p)
        assert universe == {"005930", "000660"}

    def test_zero_padding(self, tmp_path: Path):
        p = tmp_path / "list.parquet"
        _write_parquet(p, [{"code": "5930", "name": "?", "market": "KOSPI"}])
        universe = load_stock_universe(p)
        assert "005930" in universe

    def test_strips_whitespace(self, tmp_path: Path):
        p = tmp_path / "list.parquet"
        _write_parquet(p, [{"code": " 005930 ", "name": "?", "market": "KOSPI"}])
        universe = load_stock_universe(p)
        assert "005930" in universe

    def test_skips_empty_code(self, tmp_path: Path):
        p = tmp_path / "list.parquet"
        _write_parquet(
            p,
            [
                {"code": "005930", "name": "정상", "market": "KOSPI"},
                {"code": "", "name": "비정상", "market": "KOSPI"},
            ],
        )
        universe = load_stock_universe(p)
        assert universe == {"005930"}


class TestErrors:
    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_stock_universe(tmp_path / "missing.parquet")

    def test_missing_code_column(self, tmp_path: Path):
        p = tmp_path / "list.parquet"
        _write_parquet(p, [{"name": "삼성전자", "market": "KOSPI"}])
        with pytest.raises(ValueError, match="code"):
            load_stock_universe(p)
