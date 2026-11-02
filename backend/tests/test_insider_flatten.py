from __future__ import annotations

import pandas as pd
import pytest

from fakes import make_insider_filing, make_insider_tx, make_owner, make_ref
from whymoved.domain.insider import TRADE_COLUMNS, plan_label, primary_owner, trades_from_filing


def test_a_filing_becomes_one_row_per_non_derivative_transaction() -> None:
    filing = make_insider_filing(
        make_ref("a1"),
        [
            make_insider_tx("S", shares=1000.0, price=283.0),
            make_insider_tx("P", acquired=True, price=12.5),
            make_insider_tx("M", derivative=True),
        ],
    )

    rows = trades_from_filing(filing, "ACME")

    assert list(rows.columns) == TRADE_COLUMNS
    assert len(rows) == 2
    first = rows.iloc[0]
    assert first["ticker"] == "ACME"
    assert first["issuer_cik"] == 3453
    assert first["form"] == "4"
    assert first["filing_date"] == pd.Timestamp("2026-08-28")
    assert first["trade_date"] == pd.Timestamp("2026-08-27")
    assert first["code"] == "S" and first["kind"] == "open_market_sell"
    assert first["shares"] == 1000.0 and first["price"] == 283.0
    assert first["acquired"] == "D" and first["direct"] == "D"
    assert first["owner_name"] == "Doe Jane" and first["owner_role"] == "CEO"
    assert first["source"] == "xml"
    assert rows.iloc[1]["kind"] == "open_market_buy" and rows.iloc[1]["acquired"] == "A"


def test_the_universe_ticker_is_used_not_the_filers_symbol() -> None:
    filing = make_insider_filing(make_ref("a1"))

    assert set(trades_from_filing(filing, "ACMEX")["ticker"]) == {"ACMEX"}


def test_derivative_only_filings_and_amendments_give_no_rows() -> None:
    derivative_only = make_insider_filing(make_ref("a1"), [make_insider_tx("M", derivative=True)])
    amendment = make_insider_filing(make_ref("a2", form="4/A"))

    assert trades_from_filing(derivative_only, "ACME").empty
    assert trades_from_filing(amendment, "ACME").empty


@pytest.mark.parametrize(
    ("flag", "footnote", "expected"),
    [
        (True, False, "yes"),
        (False, True, "no"),
        (None, True, "yes"),
        (None, False, "unknown"),
    ],
)
def test_plan_labels(flag: bool | None, footnote: bool, expected: str) -> None:
    filing = make_insider_filing(make_ref("a1"), plan_flag=flag, footnote=footnote)

    assert plan_label(filing) == expected
    assert trades_from_filing(filing, "ACME").iloc[0]["plan"] == expected


def test_the_most_senior_owner_is_chosen() -> None:
    director = make_owner("Roe Rick", officer=False, director=True, title="")
    trust = make_owner("Roe Trust", officer=False, ten_percent=True, title="")
    officer = make_owner("Doe Jane", title="CFO")

    assert primary_owner([trust, director, officer]) == officer
    assert primary_owner([trust, director]) == director
    assert primary_owner([]) is None


def test_missing_details_become_empty_strings() -> None:
    filing = make_insider_filing(
        make_ref("a1"), [make_insider_tx(acquired=None, direct=None, price=None)], owners=[]
    )

    row = trades_from_filing(filing, "ACME").iloc[0]

    assert row["acquired"] == "" and row["direct"] == ""
    assert row["owner_name"] == "" and row["owner_role"] == ""
    assert pd.isna(row["price"])
