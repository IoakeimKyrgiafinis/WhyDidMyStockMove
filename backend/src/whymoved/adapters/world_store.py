"""World press articles as one parquet file sorted by ticker, plus how far the job has read.

Like the press releases, the API never loads the whole file: `read_ticker` reads the row groups
that can hold one stock.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from whymoved.adapters.parquet_store import StoreError
from whymoved.domain.world_news import WORLD_COLUMNS, empty_world, normalize_world

WORLD_FILE = "world_news.parquet"
WORLD_STATE_FILE = "world_news_state.json"
ROW_GROUP_ROWS = 2_000


class ParquetWorldStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def path(self) -> Path:
        return self._directory / WORLD_FILE

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return empty_world()
        try:
            return normalize_world(pd.read_parquet(self.path))
        except (ValueError, OSError, pa.ArrowException) as exc:
            raise StoreError(f"the world news file is unreadable: {exc}") from exc

    def save(self, frame: pd.DataFrame) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        ordered = normalize_world(frame).sort_values(["ticker", "seen_at"], kind="mergesort")
        table = pa.Table.from_pandas(ordered, preserve_index=False)
        temp = self._directory / f".{WORLD_FILE}.tmp"
        try:
            pq.write_table(table, temp, row_group_size=ROW_GROUP_ROWS)
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def read_through(self) -> datetime | None:
        """The last 15-minute file already read, or None before the first run."""
        path = self._directory / WORLD_STATE_FILE
        if not path.exists():
            return None
        try:
            return datetime.fromisoformat(str(json.loads(path.read_text("utf-8"))["through"]))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StoreError(f"the world news state is unreadable: {exc}") from exc

    def write_through(self, through: datetime) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / WORLD_STATE_FILE
        path.write_text(json.dumps({"through": through.isoformat()}), encoding="utf-8")


def read_ticker(
    path: Path, ticker: str, start: datetime | None = None, end: datetime | None = None
) -> pd.DataFrame:
    """One stock's articles, optionally only those seen in [start, end). Empty if the file is
    missing."""
    if not path.exists():
        return empty_world()
    filters: list[tuple[str, str, object]] = [("ticker", "=", ticker)]
    if start is not None:
        filters.append(("seen_at", ">=", pd.Timestamp(start)))
    if end is not None:
        filters.append(("seen_at", "<", pd.Timestamp(end)))
    try:
        table = pq.read_table(path, columns=WORLD_COLUMNS, filters=filters)
    except (pa.ArrowException, OSError) as exc:
        raise StoreError(f"the world news file is unreadable: {exc}") from exc
    return normalize_world(table.to_pandas())


def read_seen_times(path: Path, ticker: str) -> pd.Series:
    """When each of one stock's articles was seen: enough to count coverage per day without
    holding the titles."""
    if not path.exists():
        return pd.Series(dtype="datetime64[ns]")
    try:
        table = pq.read_table(path, columns=["seen_at"], filters=[("ticker", "=", ticker)])
    except (pa.ArrowException, OSError) as exc:
        raise StoreError(f"the world news file is unreadable: {exc}") from exc
    return pd.Series(pd.to_datetime(table.column("seen_at").to_pandas()), dtype="datetime64[ns]")


def coverage_start(path: Path) -> pd.Timestamp | None:
    """The earliest article in the file, from the row-group statistics (nothing is read)."""
    if not path.exists():
        return None
    try:
        meta = pq.ParquetFile(path).metadata
        index = meta.schema.names.index("seen_at")
        lows = [
            meta.row_group(i).column(index).statistics.min
            for i in range(meta.num_row_groups)
            if meta.row_group(i).column(index).statistics is not None
        ]
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise StoreError(f"the world news file is unreadable: {exc}") from exc
    return pd.Timestamp(min(lows)) if lows else None
