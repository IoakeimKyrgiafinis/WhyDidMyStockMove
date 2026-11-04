"""The economic calendar as one small parquet file."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from whymoved.adapters.parquet_store import StoreError
from whymoved.domain.macro import empty_macro, normalize_macro

MACRO_FILE = "macro_releases.parquet"


class ParquetMacroStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def path(self) -> Path:
        return self._directory / MACRO_FILE

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return empty_macro()
        try:
            return normalize_macro(pd.read_parquet(self.path))
        except (ValueError, OSError, pa.ArrowException) as exc:
            raise StoreError(f"the economic calendar file is unreadable: {exc}") from exc

    def save(self, frame: pd.DataFrame) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(normalize_macro(frame), preserve_index=False)
        temp = self._directory / f".{MACRO_FILE}.tmp"
        try:
            pq.write_table(table, temp)
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)
