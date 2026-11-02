from __future__ import annotations

import io
import zipfile
from datetime import date

import pandas as pd
import pytest

from whymoved.adapters.insider_bulk import BulkFormatError, parse_date, read_insider_zip
from whymoved.adapters.insider_source import EdgarInsiderSource
from whymoved.domain.insider import TRADE_COLUMNS

UNIVERSE = {3453: "ACME"}


def tsv(header: list[str], rows: list[list[str]]) -> str:
    return "\n".join("\t".join(row) for row in [header, *rows]) + "\n"


def build_zip(
    *,
    flag_column: bool = True,
    type_column: str = "DOCUMENT_TYPE",
    footnotes: bool = True,
    drop: str | None = None,
) -> io.BytesIO:
    sub_header = [
        "ACCESSION_NUMBER",
        "FILING_DATE",
        "PERIOD_OF_REPORT",
        type_column,
        "ISSUERCIK",
        "ISSUERNAME",
        "ISSUERTRADINGSYMBOL",
    ]
    submission_rows = [
        ["0001-26-000001", "28-AUG-2026", "26-AUG-2026", "4", "0000003453", "ACME CORP", "ACME"],
        ["0001-26-000002", "29-AUG-2026", "27-AUG-2026", "4", "0000003453", "ACME CORP", "ACME"],
        ["0001-26-000003", "29-AUG-2026", "27-AUG-2026", "4", "0000003453", "ACME CORP", "ACME"],
        ["0001-26-000004", "30-AUG-2026", "28-AUG-2026", "4/A", "0000003453", "ACME CORP", "ACME"],
        ["0001-26-000005", "30-AUG-2026", "28-AUG-2026", "3", "0000003453", "ACME CORP", "ACME"],
        ["0001-26-000006", "30-AUG-2026", "28-AUG-2026", "4", "0000009999", "OTHER CO", "OTH"],
        ["0001-26-000007", "31-AUG-2026", "28-AUG-2026", "4", "0000003453", "ACME CORP", "ACME"],
    ]
    flags = ["1", "", "0", "", "", "0", ""]
    if flag_column:
        sub_header.append("AFF10B5ONE")
        submission_rows = [[*row, flag] for row, flag in zip(submission_rows, flags, strict=True)]

    owner_header = [
        "ACCESSION_NUMBER",
        "RPTOWNERCIK",
        "RPTOWNERNAME",
        "RPTOWNER_RELATIONSHIP",
        "RPTOWNER_TITLE",
    ]
    owner_rows = [
        ["0001-26-000001", "1", "Doe Jane", "OFFICER", "Chairman & CEO"],
        ["0001-26-000002", "2", "Roe Trust", "TENPERCENTOWNER", ""],
        ["0001-26-000002", "3", "Roe Rick", "DIRECTOR", ""],
        ["0001-26-000003", "4", "Poe Pat", "OFFICER,DIRECTOR", "CFO"],
        ["0001-26-000007", "5", "Zed Zoe", "OTHER", ""],
    ]

    trans_header = [
        "ACCESSION_NUMBER",
        "NONDERIV_TRANS_SK",
        "SECURITY_TITLE",
        "TRANS_DATE",
        "TRANS_CODE",
        "TRANS_SHARES",
        "TRANS_PRICEPERSHARE",
        "TRANS_ACQUIRED_DISP_CD",
        "SHRS_OWND_FOLWNG_TRANS",
        "DIRECT_INDIRECT_OWNERSHIP",
    ]
    trans_rows = [
        [
            "0001-26-000001",
            "1",
            "Common Stock",
            "28-AUG-2026",
            "S",
            "1000",
            "283.00",
            "D",
            "9927.67",
            "D",
        ],
        [
            "0001-26-000002",
            "2",
            "Common Stock",
            "27-AUG-2026",
            "P",
            "200",
            "12.50",
            "A",
            "700",
            "D",
        ],
        ["0001-26-000002", "3", "Common Stock", "27-AUG-2026", "F", "50", "", "D", "650", "I"],
        ["0001-26-000003", "4", "Common Stock", "27-AUG-2026", "A", "500", "", "A", "1500", "D"],
        ["0001-26-000004", "5", "Common Stock", "28-AUG-2026", "S", "10", "9.00", "D", "1", "D"],
        ["0001-26-000006", "6", "Common Stock", "28-AUG-2026", "P", "10", "9.00", "A", "1", "D"],
        [
            "0001-26-000007",
            "7",
            "Common Stock",
            "28-AUG-2026",
            "S",
            "300",
            "20.00",
            "D",
            "100",
            "D",
        ],
    ]

    footnote_rows = [
        ["0001-26-000002", "F1", "Sold pursuant to a Rule 10b5-1 trading plan."],
        ["0001-26-000007", "F1", "Shares held by a family trust."],
    ]

    files = {
        "SUBMISSION.tsv": tsv(sub_header, submission_rows),
        "REPORTINGOWNER.tsv": tsv(owner_header, owner_rows),
        "NONDERIV_TRANS.tsv": tsv(trans_header, trans_rows),
    }
    if footnotes:
        files["FOOTNOTES.tsv"] = tsv(
            ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"], footnote_rows
        )
    if drop is not None:
        del files[drop]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    buffer.seek(0)
    return buffer


def trades_by_accession(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {str(acc): group.reset_index(drop=True) for acc, group in frame.groupby("accession")}


# ---- dates ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("28-AUG-2026", date(2026, 8, 28)),
        ("01-jan-2023", date(2023, 1, 1)),
        ("2026-08-28", date(2026, 8, 28)),
        ("", None),
        (None, None),
        ("garbage", None),
        ("31-FEB-2026", None),
        ("28-XXX-2026", None),
        ("12-FEB-0024", None),  # a real typo in the SEC's 2024q1 file
        ("2224-02-12", None),
    ],
)
def test_parse_date(text: str | None, expected: date | None) -> None:
    assert parse_date(text) == expected


# ---- reading a quarter ----


def test_only_form_4_filings_of_universe_issuers_are_kept() -> None:
    frame = read_insider_zip(build_zip(), UNIVERSE)

    assert list(frame.columns) == TRADE_COLUMNS
    assert sorted(set(frame["accession"])) == [
        "0001-26-000001",
        "0001-26-000002",
        "0001-26-000003",
        "0001-26-000007",
    ]
    assert len(frame) == 5  # the amendment, the Form 3 and the other issuer are excluded
    assert set(frame["ticker"]) == {"ACME"}
    assert set(frame["source"]) == {"bulk"}


def test_a_trade_row_is_parsed_in_full() -> None:
    frame = read_insider_zip(build_zip(), UNIVERSE)

    row = trades_by_accession(frame)["0001-26-000001"].iloc[0]

    assert row["issuer_cik"] == 3453
    assert row["form"] == "4"
    assert row["filing_date"] == pd.Timestamp("2026-08-28")
    assert row["trade_date"] == pd.Timestamp("2026-08-28")
    assert row["code"] == "S"
    assert row["kind"] == "open_market_sell"
    assert row["shares"] == 1000.0
    assert row["price"] == 283.0
    assert row["acquired"] == "D"
    assert row["direct"] == "D"
    assert row["shares_after"] == pytest.approx(9927.67)
    assert row["owner_name"] == "Doe Jane"
    assert row["owner_role"] == "Chairman & CEO"
    assert row["plan"] == "yes"


def test_kinds_and_missing_prices() -> None:
    by_accession = trades_by_accession(read_insider_zip(build_zip(), UNIVERSE))

    buy, tax = by_accession["0001-26-000002"].to_dict("records")
    (award,) = by_accession["0001-26-000003"].to_dict("records")

    assert buy["kind"] == "open_market_buy" and buy["price"] == 12.5
    assert tax["kind"] == "routine" and pd.isna(tax["price"]) and tax["direct"] == "I"
    assert award["kind"] == "routine" and pd.isna(award["price"])


def test_plan_labels_use_the_checkbox_then_footnotes() -> None:
    by_accession = trades_by_accession(read_insider_zip(build_zip(), UNIVERSE))

    assert by_accession["0001-26-000001"]["plan"].iloc[0] == "yes"  # checkbox 1
    assert by_accession["0001-26-000002"]["plan"].iloc[0] == "yes"  # no checkbox, footnote
    assert by_accession["0001-26-000003"]["plan"].iloc[0] == "no"  # checkbox 0
    assert by_accession["0001-26-000007"]["plan"].iloc[0] == "unknown"


def test_the_most_senior_owner_represents_a_joint_filing() -> None:
    by_accession = trades_by_accession(read_insider_zip(build_zip(), UNIVERSE))

    joint = by_accession["0001-26-000002"].iloc[0]
    officer_and_director = by_accession["0001-26-000003"].iloc[0]
    other = by_accession["0001-26-000007"].iloc[0]

    assert (joint["owner_name"], joint["owner_role"]) == ("Roe Rick", "Director")
    assert officer_and_director["owner_role"] == "CFO"
    assert other["owner_role"] == "Other"


def test_older_quarters_without_the_checkbox_column() -> None:
    by_accession = trades_by_accession(read_insider_zip(build_zip(flag_column=False), UNIVERSE))

    assert by_accession["0001-26-000001"]["plan"].iloc[0] == "unknown"
    assert by_accession["0001-26-000002"]["plan"].iloc[0] == "yes"  # via the footnote


def test_the_form_type_column_name_is_accepted() -> None:
    frame = read_insider_zip(build_zip(type_column="FORM_TYPE"), UNIVERSE)

    assert len(frame) == 5


def test_the_footnotes_table_is_optional() -> None:
    by_accession = trades_by_accession(read_insider_zip(build_zip(footnotes=False), UNIVERSE))

    assert by_accession["0001-26-000002"]["plan"].iloc[0] == "unknown"


def test_no_matching_issuers_gives_an_empty_table() -> None:
    frame = read_insider_zip(build_zip(), {777: "NOPE"})

    assert list(frame.columns) == TRADE_COLUMNS
    assert frame.empty


def test_the_ticker_comes_from_the_mapping() -> None:
    frame = read_insider_zip(build_zip(), {3453: "ACMEX"})

    assert set(frame["ticker"]) == {"ACMEX"}


def test_broken_archives_are_reported() -> None:
    with pytest.raises(BulkFormatError, match="ZIP"):
        read_insider_zip(io.BytesIO(b"not a zip"), UNIVERSE)
    with pytest.raises(BulkFormatError, match="SUBMISSION"):
        read_insider_zip(build_zip(drop="SUBMISSION.tsv"), UNIVERSE)
    with pytest.raises(BulkFormatError, match="NONDERIV_TRANS"):
        read_insider_zip(build_zip(drop="NONDERIV_TRANS.tsv"), UNIVERSE)


def test_missing_required_columns_are_reported() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SUBMISSION.tsv", tsv(["ACCESSION_NUMBER"], [["a"]]))
        archive.writestr("REPORTINGOWNER.tsv", tsv(["ACCESSION_NUMBER"], []))
        archive.writestr("NONDERIV_TRANS.tsv", tsv(["ACCESSION_NUMBER", "TRANS_CODE"], []))
    buffer.seek(0)

    with pytest.raises(BulkFormatError, match="missing columns"):
        read_insider_zip(buffer, UNIVERSE)


# ---- the source adapter ----


class FakeDownloader:
    def __init__(self, payload: bytes | None) -> None:
        self.payload = payload
        self.calls: list[tuple[int, int]] = []

    def insider_dataset(self, year: int, quarter: int) -> bytes | None:
        self.calls.append((year, quarter))
        return self.payload


def test_the_source_parses_what_the_downloader_returns() -> None:
    downloader = FakeDownloader(build_zip().getvalue())

    frame = EdgarInsiderSource(downloader, UNIVERSE).trades(2026, 3)

    assert frame is not None
    assert len(frame) == 5
    assert downloader.calls == [(2026, 3)]


def test_the_source_passes_a_missing_quarter_through() -> None:
    assert EdgarInsiderSource(FakeDownloader(None), UNIVERSE).trades(2026, 4) is None
