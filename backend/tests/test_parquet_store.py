from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from fakes import make_world
from helpers import as_float
from whymoved.adapters.parquet_store import CLOSE_FILE, VOLUME_FILE, ParquetPriceStore, StoreError
from whymoved.domain.bars import DailyBars


def sample() -> DailyBars:
    close, volume = make_world(60)
    return DailyBars(close=close, volume=volume)


def test_an_empty_store_loads_as_none(tmp_path: Path) -> None:
    assert ParquetPriceStore(tmp_path / "data").load() is None


def test_what_is_saved_loads_back_unchanged(tmp_path: Path) -> None:
    bars = sample()
    store = ParquetPriceStore(tmp_path / "data")

    store.save(bars)
    loaded = store.load()

    assert loaded is not None
    pd.testing.assert_frame_equal(
        loaded.close, bars.close, check_freq=False, check_index_type=False
    )
    pd.testing.assert_frame_equal(
        loaded.volume, bars.volume, check_freq=False, check_index_type=False
    )
    assert list((tmp_path / "data").glob("*.tmp")) == []


def test_saving_again_replaces_the_data(tmp_path: Path) -> None:
    bars = sample()
    store = ParquetPriceStore(tmp_path)
    store.save(bars)

    store.save(DailyBars(close=bars.close * 2.0, volume=bars.volume))
    loaded = store.load()

    assert loaded is not None
    assert as_float(loaded.close.iloc[0, 0]) == pytest.approx(as_float(bars.close.iloc[0, 0]) * 2.0)


def test_an_incomplete_store_is_reported(tmp_path: Path) -> None:
    store = ParquetPriceStore(tmp_path)
    store.save(sample())
    (tmp_path / VOLUME_FILE).unlink()

    with pytest.raises(StoreError, match="incomplete"):
        store.load()


def test_an_inconsistent_store_is_reported(tmp_path: Path) -> None:
    store = ParquetPriceStore(tmp_path)
    bars = sample()
    store.save(bars)
    bars.volume.iloc[:30].to_parquet(tmp_path / VOLUME_FILE)  # fewer dates than the closes

    with pytest.raises(StoreError, match="inconsistent"):
        store.load()


def test_a_failed_write_keeps_the_previous_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bars = sample()
    store = ParquetPriceStore(tmp_path)
    store.save(bars)

    original = pd.DataFrame.to_parquet
    calls = {"count": 0}

    def flaky(self: pd.DataFrame, *args: Any, **kwargs: Any) -> Any:
        calls["count"] += 1
        if calls["count"] == 2:  # the second file of the next save
            raise OSError("disk full")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", flaky)
    with pytest.raises(OSError, match="disk full"):
        store.save(DailyBars(close=bars.close * 2.0, volume=bars.volume))

    loaded = store.load()
    assert loaded is not None
    pd.testing.assert_frame_equal(
        loaded.close, bars.close, check_freq=False, check_index_type=False
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([CLOSE_FILE, VOLUME_FILE])
