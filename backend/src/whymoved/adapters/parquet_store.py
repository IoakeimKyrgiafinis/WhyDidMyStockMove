from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from whymoved.domain.bars import DailyBars

CLOSE_FILE = "prices_close.parquet"
VOLUME_FILE = "prices_volume.parquet"


class StoreError(Exception):
    """The price store is incomplete, unreadable or inconsistent."""


class ParquetPriceStore:
    """Daily closes and volumes as two parquet files in one directory.

    Each save writes both files to temporary names first and only then renames them over
    the old ones, so a failed write does not damage the existing data.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def load(self) -> DailyBars | None:
        close_path = self._directory / CLOSE_FILE
        volume_path = self._directory / VOLUME_FILE
        present = (close_path.exists(), volume_path.exists())
        if present == (False, False):
            return None
        if present != (True, True):
            raise StoreError("the price store is incomplete: one of its two files is missing")
        try:
            return DailyBars(close=pd.read_parquet(close_path), volume=pd.read_parquet(volume_path))
        except (ValueError, OSError) as exc:
            raise StoreError(f"the price store is unreadable or inconsistent: {exc}") from exc

    def save(self, bars: DailyBars) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        frames = {CLOSE_FILE: bars.close, VOLUME_FILE: bars.volume}
        temps: dict[str, Path] = {}
        try:
            for name, frame in frames.items():
                temp = self._directory / f".{name}.tmp"
                temps[name] = temp
                frame.to_parquet(temp, engine="pyarrow")
            for name, temp in temps.items():
                os.replace(temp, self._directory / name)
        finally:
            for temp in temps.values():
                temp.unlink(missing_ok=True)
