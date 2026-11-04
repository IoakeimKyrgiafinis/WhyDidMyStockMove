"""Press releases as one parquet file, sorted by ticker in small row groups.

The API never loads the whole file: `read_ticker` uses the row groups' ticker statistics to
read only the groups that can hold one stock, so the text stays out of the server's memory.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from whymoved.adapters.parquet_store import StoreError
from whymoved.domain.press import PRESS_COLUMNS, empty_press, normalize_press

PRESS_FILE = "press_releases.parquet"
ROW_GROUP_ROWS = 1_000


class ParquetPressStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def path(self) -> Path:
        return self._directory / PRESS_FILE

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return empty_press()
        try:
            return normalize_press(pd.read_parquet(self.path))
        except (ValueError, OSError) as exc:
            raise StoreError(f"the press-release file is unreadable: {exc}") from exc

    def save(self, press: pd.DataFrame) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        ordered = normalize_press(press).sort_values(["ticker", "accession"], kind="mergesort")
        table = pa.Table.from_pandas(ordered, preserve_index=False)
        temp = self._directory / f".{PRESS_FILE}.tmp"
        try:
            pq.write_table(table, temp, row_group_size=ROW_GROUP_ROWS)
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)


def read_ticker(path: Path, ticker: str) -> pd.DataFrame:
    """One stock's rows (only those with a readable release). Empty if the file is missing."""
    if not path.exists():
        return empty_press()
    try:
        table = pq.read_table(
            path,
            columns=PRESS_COLUMNS,
            filters=[("ticker", "=", ticker), ("title", "!=", "")],
        )
    except (pa.ArrowException, OSError) as exc:
        raise StoreError(f"the press-release file is unreadable: {exc}") from exc
    return normalize_press(table.to_pandas())
