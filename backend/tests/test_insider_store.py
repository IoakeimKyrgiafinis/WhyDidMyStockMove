from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from whymoved.adapters.insider_store import (
    SIGNALS_FILE,
    STATE_FILE,
    TRADES_FILE,
    ParquetTradeStore,
    load_imported,
    save_imported,
)
from whymoved.adapters.parquet_store import StoreError
from whymoved.domain.insider import TRADE_COLUMNS, empty_trades, normalize_trades


def sample() -> pd.DataFrame:
    rows = [
        {
            "accession": "a1",
            "issuer_cik": 3453,
            "ticker": "ACME",
            "form": "4",
            "filing_date": date(2026, 8, 28),
            "trade_date": date(2026, 8, 27),
            "code": "S",
            "kind": "open_market_sell",
            "shares": 100.0,
            "price": 10.0,
            "acquired": "D",
            "direct": "D",
            "shares_after": 500.0,
            "owner_name": "Doe Jane",
            "owner_role": "CEO",
            "plan": "yes",
            "source": "bulk",
        },
        {
            "accession": "a2",
            "issuer_cik": 3453,
            "ticker": "ACME",
            "form": "4",
            "filing_date": date(2026, 8, 29),
            "trade_date": None,
            "code": "A",
            "kind": "routine",
            "shares": 50.0,
            "price": None,
            "acquired": "A",
            "direct": "",
            "shares_after": None,
            "owner_name": "",
            "owner_role": "",
            "plan": "unknown",
            "source": "bulk",
        },
    ]
    return normalize_trades(pd.DataFrame(rows, columns=TRADE_COLUMNS))


def test_an_empty_store_loads_an_empty_table(tmp_path: Path) -> None:
    pd.testing.assert_frame_equal(ParquetTradeStore(tmp_path / "data").load(), empty_trades())


def test_what_is_saved_loads_back_unchanged(tmp_path: Path) -> None:
    store = ParquetTradeStore(tmp_path / "data")

    store.save(sample())

    pd.testing.assert_frame_equal(store.load(), sample())
    assert list((tmp_path / "data").glob("*.tmp")) == []


def test_an_empty_table_round_trips(tmp_path: Path) -> None:
    store = ParquetTradeStore(tmp_path)

    store.save(empty_trades())

    assert store.load().empty


def test_an_unreadable_file_is_reported(tmp_path: Path) -> None:
    (tmp_path / TRADES_FILE).write_bytes(b"this is not parquet")

    with pytest.raises(StoreError, match="unreadable"):
        ParquetTradeStore(tmp_path).load()


def test_a_file_with_the_wrong_columns_is_reported(tmp_path: Path) -> None:
    pd.DataFrame({"accession": ["a"]}).to_parquet(tmp_path / TRADES_FILE)

    with pytest.raises(StoreError, match="malformed"):
        ParquetTradeStore(tmp_path).load()


def test_a_failed_write_keeps_the_previous_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ParquetTradeStore(tmp_path)
    store.save(sample())

    def failing(self: pd.DataFrame, *args: Any, **kwargs: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", failing)
    with pytest.raises(OSError, match="disk full"):
        store.save(sample().iloc[:1])

    pd.testing.assert_frame_equal(store.load(), sample())
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([SIGNALS_FILE, TRADES_FILE])


def test_imported_quarters_round_trip(tmp_path: Path) -> None:
    save_imported(tmp_path / "data", {"2026q2", "2025q4"})

    assert load_imported(tmp_path / "data") == {"2025q4", "2026q2"}


def test_missing_state_means_nothing_imported(tmp_path: Path) -> None:
    assert load_imported(tmp_path) == set()


def test_bad_state_files_are_reported(tmp_path: Path) -> None:
    (tmp_path / STATE_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError, match="cannot read"):
        load_imported(tmp_path)

    (tmp_path / STATE_FILE).write_text('{"imported": [1, 2]}', encoding="utf-8")
    with pytest.raises(StoreError, match="expected format"):
        load_imported(tmp_path)


def test_the_signals_extract_holds_only_open_market_trades(tmp_path: Path) -> None:
    trades = sample()
    ParquetTradeStore(tmp_path).save(trades)

    extract = pd.read_parquet(tmp_path / SIGNALS_FILE)

    expected = trades[trades["kind"].isin(["open_market_buy", "open_market_sell"])]
    assert len(extract) == len(expected) > 0
    assert set(extract["kind"]) <= {"open_market_buy", "open_market_sell"}
    assert len(expected) < len(trades)  # the sample has routine rows to leave out
