"""FDA and clinical-trial events as one small parquet file."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from whymoved.adapters.parquet_store import StoreError
from whymoved.domain.health import empty_health, normalize_health

HEALTH_FILE = "health_events.parquet"


class ParquetHealthStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def path(self) -> Path:
        return self._directory / HEALTH_FILE

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return empty_health()
        try:
            return normalize_health(pd.read_parquet(self.path))
        except (ValueError, OSError, pa.ArrowException) as exc:
            raise StoreError(f"the health events file is unreadable: {exc}") from exc

    def save(self, frame: pd.DataFrame) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(normalize_health(frame), preserve_index=False)
        temp = self._directory / f".{HEALTH_FILE}.tmp"
        try:
            pq.write_table(table, temp)
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)
