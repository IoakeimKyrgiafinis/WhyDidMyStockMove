from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from whymoved.adapters.filings_store import (
    EVENTS_FILE,
    SEEN_FILE,
    ParquetEventStore,
    load_seen,
    save_seen,
)
from whymoved.adapters.parquet_store import StoreError
from whymoved.domain.corporate_events import EVENT_COLUMNS, empty_events, normalize_events


def sample() -> pd.DataFrame:
    rows = [
        {
            "ticker": "ACME",
            "cik": 3453,
            "accession": "k1",
            "form": "8-K",
            "filing_date": date(2026, 8, 20),
            "accepted_at": pd.Timestamp("2026-08-20 21:30"),
            "items": "2.02,9.01",
            "primary_document": "x.htm",
        },
        {
            "ticker": "BETA",
            "cik": 9,
            "accession": "k2",
            "form": "8-K",
            "filing_date": date(2026, 8, 21),
            "accepted_at": None,
            "items": "",
            "primary_document": "y.htm",
        },
    ]
    return normalize_events(pd.DataFrame(rows, columns=EVENT_COLUMNS))


def test_an_empty_store_loads_an_empty_table(tmp_path: Path) -> None:
    pd.testing.assert_frame_equal(ParquetEventStore(tmp_path / "data").load(), empty_events())


def test_events_round_trip(tmp_path: Path) -> None:
    store = ParquetEventStore(tmp_path / "data")

    store.save(sample())

    pd.testing.assert_frame_equal(store.load(), sample())
    assert list((tmp_path / "data").glob("*.tmp")) == []


def test_bad_event_files_are_reported(tmp_path: Path) -> None:
    (tmp_path / EVENTS_FILE).write_bytes(b"not parquet")
    with pytest.raises(StoreError, match="unreadable"):
        ParquetEventStore(tmp_path).load()

    pd.DataFrame({"ticker": ["A"]}).to_parquet(tmp_path / EVENTS_FILE)
    with pytest.raises(StoreError, match="malformed"):
        ParquetEventStore(tmp_path).load()


def test_a_failed_write_keeps_the_previous_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ParquetEventStore(tmp_path)
    store.save(sample())

    def failing(self: pd.DataFrame, *args: Any, **kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", failing)
    with pytest.raises(OSError, match="disk full"):
        store.save(sample().iloc[:1])

    pd.testing.assert_frame_equal(store.load(), sample())


def test_seen_filings_round_trip(tmp_path: Path) -> None:
    seen = {"a2": date(2026, 9, 1), "a1": date(2026, 8, 28)}

    save_seen(tmp_path / "data", seen)

    assert load_seen(tmp_path / "data") == seen


def test_missing_and_bad_seen_files(tmp_path: Path) -> None:
    assert load_seen(tmp_path) == {}

    (tmp_path / SEEN_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError, match="cannot read"):
        load_seen(tmp_path)

    (tmp_path / SEEN_FILE).write_text('{"form4_seen": {"a1": "not a date"}}', encoding="utf-8")
    with pytest.raises(StoreError, match="cannot read"):
        load_seen(tmp_path)

    (tmp_path / SEEN_FILE).write_text('{"other": 1}', encoding="utf-8")
    with pytest.raises(StoreError, match="cannot read"):
        load_seen(tmp_path)
