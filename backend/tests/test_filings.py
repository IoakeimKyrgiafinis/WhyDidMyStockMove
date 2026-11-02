from __future__ import annotations

from datetime import date

import pytest

from whymoved.domain.filings import (
    FilingRef,
    InsiderFiling,
    InsiderTransaction,
    ReportingOwner,
    TransactionKind,
    business_days_between,
    classify_transaction,
    mentions_10b5_1,
)

FRIDAY = date(2026, 8, 28)
MONDAY = date(2026, 8, 31)


def make_ref(filed: date = MONDAY) -> FilingRef:
    return FilingRef(
        cik=3453,
        accession="0001225208-26-007379",
        form="4",
        filing_date=filed,
        accepted_at=None,
        report_date=None,
        primary_document="form4.xml",
    )


def make_tx(
    code: str = "S",
    *,
    derivative: bool = False,
    when: date | None = FRIDAY,
    shares: float | None = 100.0,
    price: float | None = 10.0,
) -> InsiderTransaction:
    return InsiderTransaction(
        security_title="Common Stock",
        transaction_date=when,
        code=code,
        acquired=False,
        shares=shares,
        price=price,
        shares_after=None,
        direct=True,
        is_derivative=derivative,
    )


def make_filing(*, flag: bool | None, footnote_plan: bool, filed: date = MONDAY) -> InsiderFiling:
    return InsiderFiling(
        ref=make_ref(filed),
        issuer_cik=3453,
        issuer_name="ACME",
        issuer_ticker="ACME",
        period_of_report=FRIDAY,
        schema_version="X0508",
        owners=(),
        transactions=(),
        plan_flag=flag,
        footnote_mentions_plan=footnote_plan,
        footnotes=(),
    )


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (FRIDAY, FRIDAY, 0),
        (FRIDAY, MONDAY, 1),
        (date(2026, 8, 24), date(2026, 8, 26), 2),
        (date(2026, 8, 24), date(2026, 8, 31), 5),
        (date(2026, 8, 26), date(2026, 8, 29), 2),
    ],
)
def test_business_days_between(start: date, end: date, expected: int) -> None:
    assert business_days_between(start, end) == expected


def test_business_days_reject_a_reversed_range() -> None:
    with pytest.raises(ValueError, match="before"):
        business_days_between(MONDAY, FRIDAY)


@pytest.mark.parametrize(
    ("code", "derivative", "expected"),
    [
        ("P", False, TransactionKind.OPEN_MARKET_BUY),
        ("p", False, TransactionKind.OPEN_MARKET_BUY),
        ("S", False, TransactionKind.OPEN_MARKET_SELL),
        ("F", False, TransactionKind.ROUTINE),
        ("A", False, TransactionKind.ROUTINE),
        ("G", False, TransactionKind.ROUTINE),
        ("M", True, TransactionKind.ROUTINE),
        ("S", True, TransactionKind.OTHER),
        ("P", True, TransactionKind.OTHER),
        ("J", False, TransactionKind.OTHER),
        ("", False, TransactionKind.OTHER),
    ],
)
def test_transaction_classification(code: str, derivative: bool, expected: TransactionKind) -> None:
    assert classify_transaction(code, derivative) is expected
    assert make_tx(code, derivative=derivative).kind is expected


def test_value_needs_both_shares_and_a_positive_price() -> None:
    assert make_tx(shares=100.0, price=10.0).value == pytest.approx(1000.0)
    assert make_tx(price=None).value is None
    assert make_tx(price=0.0).value is None
    assert make_tx(shares=None).value is None


@pytest.mark.parametrize(
    ("flag", "footnote", "expected"),
    [
        (True, False, True),
        (True, True, True),
        (False, True, False),
        (False, False, False),
        (None, True, True),
        (None, False, False),
    ],
)
def test_the_plan_checkbox_decides_when_present(
    flag: bool | None, footnote: bool, expected: bool
) -> None:
    assert make_filing(flag=flag, footnote_plan=footnote).under_plan is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("effected pursuant to a Rule 10b5-1 trading plan", True),
        ("intended to comply with Rule 10b5\u20111", True),
        ("a 10b5 1 plan", True),
        ("an ordinary open-market sale", False),
    ],
)
def test_footnote_plan_detection(text: str, expected: bool) -> None:
    assert mentions_10b5_1(text) is expected


def test_filing_lag_counts_business_days() -> None:
    filing = make_filing(flag=None, footnote_plan=False)

    assert filing.filing_lag(make_tx(when=FRIDAY)) == 1
    assert filing.filing_lag(make_tx(when=date(2026, 8, 31))) == 0
    assert filing.filing_lag(make_tx(when=date(2026, 9, 2))) is None  # traded after filing
    assert filing.filing_lag(make_tx(when=None)) is None


def test_owner_roles() -> None:
    def owner(**flags: object) -> ReportingOwner:
        base: dict[str, object] = {
            "cik": 1,
            "name": "X",
            "is_director": False,
            "is_officer": False,
            "is_ten_percent_owner": False,
            "is_other": False,
            "officer_title": "",
        }
        return ReportingOwner(**{**base, **flags})  # type: ignore[arg-type]

    assert owner(is_officer=True, officer_title="CFO", is_director=True).role == "CFO"
    assert owner(is_officer=True).role == "Officer"
    assert owner(is_director=True).role == "Director"
    assert owner(is_ten_percent_owner=True).role == "10% owner"
    assert owner().role == "Other"
