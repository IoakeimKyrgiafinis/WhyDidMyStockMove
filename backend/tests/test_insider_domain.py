from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from helpers import is_missing
from whymoved.domain.insider import (
    TRADE_COLUMNS,
    empty_trades,
    normalize_trades,
    plan_quarters,
    quarter_key,
    quarters_between,
    role_label,
    role_rank,
)


def test_quarters_between() -> None:
    assert quarters_between(date(2026, 2, 10), date(2026, 11, 2)) == [
        (2026, 1),
        (2026, 2),
        (2026, 3),
        (2026, 4),
    ]
    assert quarters_between(date(2025, 11, 1), date(2026, 2, 1)) == [(2025, 4), (2026, 1)]
    assert quarters_between(date(2026, 5, 1), date(2026, 5, 20)) == [(2026, 2)]


def test_quarters_between_rejects_a_reversed_range() -> None:
    with pytest.raises(ValueError, match="before"):
        quarters_between(date(2026, 5, 1), date(2026, 4, 1))


def test_quarter_key() -> None:
    assert quarter_key(2026, 3) == "2026q3"


def test_a_first_run_plans_every_quarter() -> None:
    plan = plan_quarters(date(2024, 1, 1), date(2026, 11, 2), set())

    assert len(plan) == 12
    assert plan[0] == (2024, 1)
    assert plan[-1] == (2026, 4)


def test_the_latest_quarters_are_always_refreshed() -> None:
    imported = {"2026q1", "2026q2", "2026q3"}

    plan = plan_quarters(date(2026, 1, 1), date(2026, 11, 2), imported)

    assert plan == [(2026, 3), (2026, 4)]


def test_refresh_can_be_switched_off() -> None:
    imported = {"2026q1", "2026q2", "2026q3", "2026q4"}

    assert plan_quarters(date(2026, 1, 1), date(2026, 11, 2), imported, refresh_latest=0) == []
    with pytest.raises(ValueError, match="refresh_latest"):
        plan_quarters(date(2026, 1, 1), date(2026, 11, 2), imported, refresh_latest=-1)


@pytest.mark.parametrize(
    ("officer", "director", "ten", "title", "label", "rank"),
    [
        (True, True, False, "CFO", "CFO", 0),
        (True, False, False, "", "Officer", 0),
        (False, True, False, "", "Director", 1),
        (False, False, True, "", "10% owner", 2),
        (False, False, False, "", "Other", 3),
    ],
)
def test_roles(officer: bool, director: bool, ten: bool, title: str, label: str, rank: int) -> None:
    flags = {"officer": officer, "director": director, "ten_percent": ten}

    assert role_label(**flags, title=title) == label
    assert role_rank(**flags) == rank


def test_normalize_enforces_the_schema() -> None:
    frame = pd.DataFrame(
        [
            {
                "accession": "a1",
                "issuer_cik": "3453",
                "ticker": "ACME",
                "form": "4",
                "filing_date": date(2026, 8, 28),
                "trade_date": None,
                "code": "S",
                "kind": "open_market_sell",
                "shares": "1000",
                "price": None,
                "acquired": "D",
                "direct": "D",
                "shares_after": 5.0,
                "owner_name": "X",
                "owner_role": "CEO",
                "plan": "yes",
                "source": "bulk",
            }
        ]
    )

    out = normalize_trades(frame)

    assert list(out.columns) == TRADE_COLUMNS
    assert out["issuer_cik"].dtype == "int64"
    assert str(out["filing_date"].dtype).startswith("datetime64")
    assert is_missing(out.loc[0, "trade_date"])
    assert out.loc[0, "shares"] == 1000.0
    assert is_missing(out.loc[0, "price"])


def test_normalize_rejects_missing_columns() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        normalize_trades(pd.DataFrame({"accession": ["a"]}))


def test_the_empty_table_has_the_right_shape() -> None:
    empty = empty_trades()

    assert list(empty.columns) == TRADE_COLUMNS
    assert len(empty) == 0
    assert empty["issuer_cik"].dtype == "int64"
