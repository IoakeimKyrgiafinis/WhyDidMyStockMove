from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pandas as pd

from whymoved.adapters.parquet_store import StoreError
from whymoved.domain.corporate_events import empty_events, normalize_events

EVENTS_FILE = "events.parquet"
SEEN_FILE = "filings_seen.json"


class ParquetEventStore:
    """8-K events as one parquet file, replaced atomically."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def load(self) -> pd.DataFrame:
        path = self._directory / EVENTS_FILE
        if not path.exists():
            return empty_events()
        try:
            return normalize_events(pd.read_parquet(path))
        except (ValueError, OSError) as exc:
            raise StoreError(f"the events file is unreadable or malformed: {exc}") from exc

    def save(self, events: pd.DataFrame) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        temp = self._directory / f".{EVENTS_FILE}.tmp"
        try:
            normalize_events(events).to_parquet(temp, engine="pyarrow")
            os.replace(temp, self._directory / EVENTS_FILE)
        finally:
            temp.unlink(missing_ok=True)


def load_seen(directory: Path) -> dict[str, date]:
    """Form 4 filings already processed that produced no trade rows, with their filing date."""
    path = directory / SEEN_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data["form4_seen"]
        return {str(key): date.fromisoformat(str(value)) for key, value in raw.items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise StoreError(f"cannot read {path}: {exc}") from exc


def save_seen(directory: Path, seen: Mapping[str, date]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f".{SEEN_FILE}.tmp"
    payload = {"form4_seen": {key: value.isoformat() for key, value in sorted(seen.items())}}
    try:
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temp, directory / SEEN_FILE)
    finally:
        temp.unlink(missing_ok=True)
