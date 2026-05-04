"""Parquet 기반 DataProvider.

로컬 parquet 파일을 KIS 대신 데이터 소스로 사용. 백테스트 sweep 용도.
"""

from .data import ParquetDataProvider, DEFAULT_DATA_ROOT

__all__ = ["ParquetDataProvider", "DEFAULT_DATA_ROOT"]
