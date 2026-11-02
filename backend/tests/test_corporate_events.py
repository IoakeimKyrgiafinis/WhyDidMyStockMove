from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from fakes import make_ref
from helpers import is_missing
from whymoved.domain.corporate_events import (
    EVENT_COLUMNS,
    empty_events,
    event_row,
    is_earnings,
    item_label,
    normalize_events,
)


def test_item_labels() -> None:
    assert item_label("2.02").startswith("Results of operations")
    assert item_label(" 5.02 ").startswith("Departure")
    assert item_label("9.9") == "Item 9.9"


@pytest.mark.parametrize(
    ("items", "expected"),
    [("2.02,9.01", True), ("2.02", True), ("5.02, 9.01", False), ("", False), ("12.02", False)],
)
def test_earnings_detection(items: str, expected: bool) -> None:
    assert is_earnings(items) is expected


def test_an_event_row_and_normalisation() -> None:
    ref = make_ref("a1", "8-K", date(2026, 8, 20), items=("2.02", "9.01"))
    ref = ref.__class__(**{**ref.__dict__, "accepted_at": datetime(2026, 8, 20, 21, 30)})

    row = event_row("ACME", ref)
    frame = normalize_events(pd.DataFrame([row], columns=EVENT_COLUMNS))

    assert row["items"] == "2.02,9.01"
    assert list(frame.columns) == EVENT_COLUMNS
    assert frame["cik"].dtype == "int64"
    assert frame.loc[0, "accepted_at"] == pd.Timestamp("2026-08-20 21:30")
    assert frame.loc[0, "filing_date"] == pd.Timestamp("2026-08-20")


def test_missing_acceptance_times_become_nat() -> None:
    row = event_row("ACME", make_ref("a1", "8-K", date(2026, 8, 20)))

    frame = normalize_events(pd.DataFrame([row], columns=EVENT_COLUMNS))

    assert is_missing(frame.loc[0, "accepted_at"])


def test_the_empty_table_and_missing_columns() -> None:
    assert list(empty_events().columns) == EVENT_COLUMNS
    assert empty_events().empty
    with pytest.raises(ValueError, match="missing columns"):
        normalize_events(pd.DataFrame({"ticker": ["A"]}))
