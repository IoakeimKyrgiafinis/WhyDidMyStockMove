from __future__ import annotations

from datetime import date, datetime

import pytest

from whymoved.adapters.edgar_errors import EdgarFormatError
from whymoved.adapters.edgar_parse import (
    SubmissionPage,
    parse_company_tickers,
    parse_filing_columns,
    parse_form4,
    parse_submissions,
    raw_xml_document,
)
from whymoved.domain.filings import FilingRef, TransactionKind

REF = FilingRef(
    cik=3453,
    accession="0001225208-26-007379",
    form="4",
    filing_date=date(2026, 8, 31),
    accepted_at=None,
    report_date=None,
    primary_document="form4.xml",
)

PLAN_FOOTNOTE = (
    '<footnotes><footnote id="F1">The sale was effected pursuant to a Rule 10b5-1 trading '
    "plan adopted on March 9, 2026.</footnote></footnotes>"
)


# ---- builders shaped like real Form 4 documents ----


def owner_xml(
    name: str,
    *,
    director: bool = False,
    officer: bool = False,
    ten_percent: bool = False,
    title: str = "",
) -> str:
    title_xml = f"<officerTitle>{title}</officerTitle>" if title else ""
    return (
        "<reportingOwner><reportingOwnerId><rptOwnerCik>0001234567</rptOwnerCik>"
        f"<rptOwnerName>{name}</rptOwnerName></reportingOwnerId>"
        "<reportingOwnerRelationship>"
        f"<isDirector>{int(director)}</isDirector><isOfficer>{int(officer)}</isOfficer>"
        f"<isTenPercentOwner>{int(ten_percent)}</isTenPercentOwner><isOther>0</isOther>"
        f"{title_xml}</reportingOwnerRelationship></reportingOwner>"
    )


def nd_tx(
    code: str,
    *,
    shares: str | None = "1000",
    price: str | None = "10.50",
    ad: str = "A",
    day: str = "2026-08-26",
    after: str | None = "5000",
    ownership: str = "D",
) -> str:
    shares_xml = (
        f"<transactionShares><value>{shares}</value></transactionShares>"
        if shares is not None
        else ""
    )
    price_xml = (
        f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare>"
        if price is not None
        else '<transactionPricePerShare><footnoteId id="F2"/></transactionPricePerShare>'
    )
    after_xml = (
        "<postTransactionAmounts><sharesOwnedFollowingTransaction>"
        f"<value>{after}</value></sharesOwnedFollowingTransaction></postTransactionAmounts>"
        if after is not None
        else ""
    )
    return (
        "<nonDerivativeTransaction>"
        "<securityTitle><value>Common Stock</value></securityTitle>"
        f"<transactionDate><value>{day}</value></transactionDate>"
        "<transactionCoding><transactionFormType>4</transactionFormType>"
        f"<transactionCode>{code}</transactionCode></transactionCoding>"
        f"<transactionAmounts>{shares_xml}{price_xml}"
        f"<transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>"
        f"</transactionAmounts>{after_xml}"
        "<ownershipNature><directOrIndirectOwnership>"
        f"<value>{ownership}</value></directOrIndirectOwnership></ownershipNature>"
        "</nonDerivativeTransaction>"
    )


def d_tx(code: str) -> str:
    return (
        "<derivativeTransaction>"
        "<securityTitle><value>Stock Option (Right to Buy)</value></securityTitle>"
        "<transactionDate><value>2026-08-26</value></transactionDate>"
        "<transactionCoding><transactionFormType>4</transactionFormType>"
        f"<transactionCode>{code}</transactionCode></transactionCoding>"
        "<transactionAmounts><transactionShares><value>500</value></transactionShares>"
        "<transactionPricePerShare><value>0</value></transactionPricePerShare>"
        "<transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>"
        "</transactionAmounts></derivativeTransaction>"
    )


def document(owners: str, nonderivative: str = "", derivative: str = "", extra: str = "") -> str:
    return (
        '<?xml version="1.0"?>'
        "<ownershipDocument><schemaVersion>X0508</schemaVersion><documentType>4</documentType>"
        "<periodOfReport>2026-08-26</periodOfReport>"
        "<issuer><issuerCik>0000003453</issuerCik><issuerName>ACME CORP</issuerName>"
        "<issuerTradingSymbol>ACME</issuerTradingSymbol></issuer>"
        f"{owners}{extra}"
        f"<nonDerivativeTable>{nonderivative}</nonDerivativeTable>"
        f"<derivativeTable>{derivative}</derivativeTable>"
        "</ownershipDocument>"
    )


# ---- Form 4 parsing ----


def test_a_planned_sale_is_parsed_in_full() -> None:
    xml = document(
        owner_xml("Doe Jane", officer=True, title="Chairman &amp; CEO"),
        nd_tx("S", shares="1000", price="283.0000", ad="D", day="2026-08-28", after="9927.6720"),
        extra="<aff10b5One>1</aff10b5One>" + PLAN_FOOTNOTE,
    )

    filing = parse_form4(xml, REF)

    assert filing.issuer_cik == 3453
    assert filing.issuer_name == "ACME CORP"
    assert filing.issuer_ticker == "ACME"
    assert filing.schema_version == "X0508"
    assert filing.period_of_report == date(2026, 8, 26)
    assert filing.plan_flag is True
    assert filing.under_plan is True
    (owner,) = filing.owners
    assert owner.name == "Doe Jane"
    assert owner.cik == 1234567
    assert owner.is_officer and not owner.is_director
    assert owner.role == "Chairman & CEO"
    (transaction,) = filing.transactions
    assert transaction.kind is TransactionKind.OPEN_MARKET_SELL
    assert transaction.shares == 1000.0
    assert transaction.price == 283.0
    assert transaction.value == pytest.approx(283_000.0)
    assert transaction.acquired is False
    assert transaction.shares_after == pytest.approx(9927.672)
    assert transaction.direct is True
    assert transaction.transaction_date == date(2026, 8, 28)
    assert filing.filing_lag(transaction) == 1
    assert filing.footnotes[0].startswith("The sale was effected")
    assert filing.footnote_mentions_plan is True


def test_a_purchase_from_a_filing_without_the_plan_checkbox() -> None:
    xml = document(owner_xml("Roe Rick", director=True), nd_tx("P", price="12.5"))

    filing = parse_form4(xml, REF)

    assert filing.plan_flag is None
    assert filing.under_plan is False
    assert filing.owners[0].role == "Director"
    (transaction,) = filing.transactions
    assert transaction.kind is TransactionKind.OPEN_MARKET_BUY
    assert transaction.acquired is True
    assert transaction.price == 12.5


def test_the_checkbox_accepts_true_and_false_spellings() -> None:
    owners = owner_xml("Doe Jane", officer=True)

    off = parse_form4(document(owners, nd_tx("F"), extra="<aff10b5One>false</aff10b5One>"), REF)
    on = parse_form4(document(owners, nd_tx("S"), extra="<aff10b5One>true</aff10b5One>"), REF)

    assert off.plan_flag is False
    assert on.plan_flag is True


def test_an_award_whose_price_is_only_a_footnote_reference() -> None:
    xml = document(owner_xml("Doe Jane", officer=True), nd_tx("A", price=None))

    (transaction,) = parse_form4(xml, REF).transactions

    assert transaction.price is None
    assert transaction.value is None
    assert transaction.kind is TransactionKind.ROUTINE


def test_derivative_rows_follow_the_non_derivative_rows() -> None:
    xml = document(owner_xml("Doe Jane", officer=True), nd_tx("F"), d_tx("M"))

    first, second = parse_form4(xml, REF).transactions

    assert first.is_derivative is False
    assert first.kind is TransactionKind.ROUTINE
    assert second.is_derivative is True
    assert second.kind is TransactionKind.ROUTINE
    assert second.security_title == "Stock Option (Right to Buy)"


def test_joint_filers_are_all_listed() -> None:
    owners = owner_xml("Doe Jane", officer=True, title="CFO") + owner_xml(
        "Doe Trust", ten_percent=True
    )

    filing = parse_form4(document(owners, nd_tx("P")), REF)

    assert [owner.name for owner in filing.owners] == ["Doe Jane", "Doe Trust"]
    assert filing.owners[1].role == "10% owner"


def test_missing_optional_fields_are_tolerated() -> None:
    xml = (
        "<ownershipDocument><nonDerivativeTable><nonDerivativeTransaction>"
        "<transactionCoding><transactionCode>p</transactionCode></transactionCoding>"
        "</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"
    )

    filing = parse_form4(xml, REF)

    (transaction,) = filing.transactions
    assert transaction.code == "P"
    assert transaction.shares is None and transaction.price is None
    assert transaction.transaction_date is None
    assert transaction.acquired is None and transaction.direct is None
    assert filing.owners == ()
    assert filing.issuer_ticker == ""


def test_bytes_are_accepted_too() -> None:
    xml = document(owner_xml("Doe Jane", officer=True), nd_tx("P"))

    assert len(parse_form4(xml.encode("utf-8"), REF).transactions) == 1


def test_broken_documents_raise_a_format_error() -> None:
    entity_bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;">]><ownershipDocument>&lol2;</ownershipDocument>'
    )

    with pytest.raises(EdgarFormatError, match="parsed"):
        parse_form4("<ownershipDocument><issuer>", REF)
    with pytest.raises(EdgarFormatError, match="parsed"):
        parse_form4("", REF)
    with pytest.raises(EdgarFormatError, match="parsed"):
        parse_form4(entity_bomb, REF)
    with pytest.raises(EdgarFormatError, match="ownershipDocument"):
        parse_form4("<html><body>maintenance</body></html>", REF)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("xslF345X05/form4.xml", "form4.xml"),
        ("xslF345X03/doc4.xml", "doc4.xml"),
        ("doc4.xml", "doc4.xml"),
        ("form4.xml", "form4.xml"),
        ("folder/file.xml", "folder/file.xml"),
    ],
)
def test_raw_xml_document(name: str, expected: str) -> None:
    assert raw_xml_document(name) == expected


# ---- submissions lists ----


def make_columns(rows: list[tuple[str, str, str, str, str]]) -> dict[str, list[object]]:
    """Rows are (accession, filing date, form, primary document, items)."""
    return {
        "accessionNumber": [row[0] for row in rows],
        "filingDate": [row[1] for row in rows],
        "reportDate": [row[1] for row in rows],
        "acceptanceDateTime": [f"{row[1]}T21:30:00.000Z" for row in rows],
        "form": [row[2] for row in rows],
        "primaryDocument": [row[3] for row in rows],
        "items": [row[4] for row in rows],
    }


ROWS = [
    ("0000000001-26-000001", "2026-08-31", "4", "xslF345X05/form4.xml", ""),
    ("0000000001-26-000002", "2026-08-20", "8-K", "acme8k.htm", "2.02,9.01"),
]


def test_filing_columns_become_references() -> None:
    first, second = parse_filing_columns(make_columns(ROWS), 3453)

    assert first.cik == 3453
    assert first.accession == "0000000001-26-000001"
    assert first.form == "4"
    assert first.filing_date == date(2026, 8, 31)
    assert first.accepted_at == datetime(2026, 8, 31, 21, 30)
    assert first.report_date == date(2026, 8, 31)
    assert first.primary_document == "xslF345X05/form4.xml"
    assert first.items == ()
    assert second.items == ("2.02", "9.01")


def test_blank_report_dates_and_list_items_are_handled() -> None:
    columns = make_columns(ROWS)
    columns["reportDate"][0] = ""
    columns["items"][1] = ["5.02", "9.01"]

    first, second = parse_filing_columns(columns, 3453)

    assert first.report_date is None
    assert second.items == ("5.02", "9.01")


def test_optional_columns_may_be_absent() -> None:
    columns = make_columns(ROWS)
    for name in ("reportDate", "acceptanceDateTime", "items"):
        del columns[name]

    first, _ = parse_filing_columns(columns, 3453)

    assert first.report_date is None
    assert first.accepted_at is None
    assert first.items == ()


def test_malformed_columns_are_rejected() -> None:
    short = make_columns(ROWS)
    short["form"] = ["4"]
    missing = make_columns(ROWS)
    del missing["accessionNumber"]
    bad_date = make_columns(ROWS)
    bad_date["filingDate"][0] = "soon"
    not_a_list: dict[str, object] = {**make_columns(ROWS), "form": "4"}

    with pytest.raises(EdgarFormatError, match="different length"):
        parse_filing_columns(short, 3453)
    with pytest.raises(EdgarFormatError, match="missing a required column"):
        parse_filing_columns(missing, 3453)
    with pytest.raises(EdgarFormatError, match="no usable"):
        parse_filing_columns(bad_date, 3453)
    with pytest.raises(EdgarFormatError, match="not a list"):
        parse_filing_columns(not_a_list, 3453)


def test_submissions_list_recent_filings_and_older_pages() -> None:
    payload = {
        "cik": "3453",
        "filings": {
            "recent": make_columns(ROWS),
            "files": [
                {
                    "name": "CIK0000003453-submissions-001.json",
                    "filingCount": 10,
                    "filingFrom": "2015-01-01",
                    "filingTo": "2022-12-31",
                }
            ],
        },
    }

    refs, pages = parse_submissions(payload, 3453)

    assert len(refs) == 2
    assert pages == [
        SubmissionPage(
            name="CIK0000003453-submissions-001.json",
            filing_from=date(2015, 1, 1),
            filing_to=date(2022, 12, 31),
        )
    ]


def test_malformed_submissions_are_rejected() -> None:
    with pytest.raises(EdgarFormatError):
        parse_submissions({"cik": "1"}, 1)
    with pytest.raises(EdgarFormatError):
        parse_submissions(["nope"], 1)
    with pytest.raises(EdgarFormatError, match="older page"):
        parse_submissions(
            {"filings": {"recent": make_columns(ROWS), "files": [{"name": "x.json"}]}}, 1
        )


# ---- ticker map ----


def test_company_tickers_are_normalised_and_cleaned() -> None:
    payload = {
        "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
        "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY"},
        "2": {"cik_str": "320193", "ticker": "AAPL", "title": "APPLE INC"},
        "3": {"cik_str": 1, "ticker": "NVDA", "title": "DUPLICATE"},
        "4": {"cik_str": 5, "ticker": "bad ticker!", "title": "JUNK"},
        "5": "not an entry",
        "6": {"cik_str": True, "ticker": "TRUE", "title": "BOOLEAN CIK"},
    }

    assert parse_company_tickers(payload) == {"NVDA": 1045810, "BRK.B": 1067983, "AAPL": 320193}
