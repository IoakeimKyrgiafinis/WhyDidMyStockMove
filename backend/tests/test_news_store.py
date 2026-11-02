from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from whymoved.adapters.news_store import NEWS_FILE, ParquetNewsStore
from whymoved.adapters.parquet_store import StoreError
from whymoved.domain.news import NewsItem


def sample() -> list[NewsItem]:
    return [
        NewsItem(
            id=1,
            headline="NVDA jumps",
            source="benzinga",
            url="https://example.com/1",
            created_at=datetime(2026, 10, 29, 14, 30),
            updated_at=datetime(2026, 10, 29, 15, 0),
            symbols=("NVDA", "BRK.B"),
        ),
        NewsItem(
            id=2,
            headline="Market wrap",
            source="",
            url="",
            created_at=datetime(2026, 10, 29, 16, 0),
            updated_at=None,
            symbols=(),
        ),
    ]


def test_an_empty_store_loads_as_an_empty_list(tmp_path: Path) -> None:
    assert ParquetNewsStore(tmp_path / "data").load() == []


def test_what_is_saved_loads_back_unchanged(tmp_path: Path) -> None:
    store = ParquetNewsStore(tmp_path / "data")

    store.save(sample())

    assert store.load() == sample()
    assert list((tmp_path / "data").glob("*.tmp")) == []


def test_an_empty_list_round_trips(tmp_path: Path) -> None:
    store = ParquetNewsStore(tmp_path)

    store.save([])

    assert store.load() == []


def test_saving_again_replaces_the_data(tmp_path: Path) -> None:
    store = ParquetNewsStore(tmp_path)
    store.save(sample())

    store.save(sample()[:1])

    assert store.load() == sample()[:1]


def test_an_unreadable_file_is_reported(tmp_path: Path) -> None:
    (tmp_path / NEWS_FILE).write_bytes(b"this is not a parquet file")

    with pytest.raises(StoreError, match="unreadable"):
        ParquetNewsStore(tmp_path).load()


def test_missing_columns_are_reported(tmp_path: Path) -> None:
    pd.DataFrame({"id": [1]}).to_parquet(tmp_path / NEWS_FILE)

    with pytest.raises(StoreError, match="missing columns"):
        ParquetNewsStore(tmp_path).load()


def test_a_failed_write_keeps_the_previous_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ParquetNewsStore(tmp_path)
    store.save(sample())

    def failing(self: pd.DataFrame, *args: Any, **kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", failing)
    with pytest.raises(OSError, match="disk full"):
        store.save(sample()[:1])

    assert store.load() == sample()
    assert [p.name for p in tmp_path.iterdir()] == [NEWS_FILE]
