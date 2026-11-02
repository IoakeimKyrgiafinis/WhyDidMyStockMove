from __future__ import annotations

import csv
import math
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import IO

import pandas as pd

from whymoved.domain.filings import classify_transaction, mentions_10b5_1
from whymoved.domain.insider import (
    TRADE_COLUMNS,
    empty_trades,
    normalize_trades,
    role_label,
    role_rank,
)

_MONTHS = {
    name: number
    for number, name in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
        start=1,
    )
}

SUBMISSION_COLUMNS = (
    "ACCESSION_NUMBER",
    "FILING_DATE",
    "DOCUMENT_TYPE",
    "FORM_TYPE",
    "ISSUERCIK",
    "AFF10B5ONE",
)
OWNER_COLUMNS = ("ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE")
TRANS_COLUMNS = (
    "ACCESSION_NUMBER",
    "TRANS_DATE",
    "TRANS_CODE",
    "TRANS_SHARES",
    "TRANS_PRICEPERSHARE",
    "TRANS_ACQUIRED_DISP_CD",
    "SHRS_OWND_FOLWNG_TRANS",
    "DIRECT_INDIRECT_OWNERSHIP",
)
FOOTNOTE_COLUMNS = ("ACCESSION_NUMBER", "FOOTNOTE_TXT")


class BulkFormatError(Exception):
    """The insider data set is not shaped as documented."""


@dataclass(frozen=True)
class _Issuer:
    cik: int
    ticker: str
    filed: date
    flag: str  # the 10b5-1 checkbox as written ("1", "0", "true", "false") or ""


def parse_date(text: str | None) -> date | None:
    """A date written DD-MON-YYYY (as in the SEC files) or YYYY-MM-DD. Locale independent."""
    if text is None:
        return None
    parts = text.strip().split("-")
    if len(parts) != 3:
        return None
    try:
        if len(parts[0]) == 4:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        month = _MONTHS.get(parts[1].upper())
        if month is None:
            return None
        return date(int(parts[2]), month, int(parts[0]))
    except ValueError:
        return None


def _number(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        number = float(value.replace(",", "").strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _int(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _find_member(archive: zipfile.ZipFile, name: str) -> str | None:
    target = f"{name}.tsv".lower()
    for member in archive.namelist():
        if member.lower().rsplit("/", 1)[-1] == target:
            return member
    return None


def _read_table(
    archive: zipfile.ZipFile, name: str, columns: Sequence[str], required: Sequence[str]
) -> pd.DataFrame:
    member = _find_member(archive, name)
    if member is None:
        raise BulkFormatError(f"{name}.tsv was not found in the archive")
    wanted = set(columns)
    with archive.open(member) as handle:
        frame = pd.read_csv(
            handle,
            sep="\t",
            dtype=str,
            na_filter=False,
            quoting=csv.QUOTE_NONE,
            usecols=lambda column: column in wanted,
            encoding="utf-8-sig",
            encoding_errors="replace",
        )
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise BulkFormatError(f"{name}.tsv is missing columns: {missing}")
    return frame


def _issuers(
    submission: pd.DataFrame, type_column: str, ticker_by_cik: Mapping[int, str]
) -> dict[str, _Issuer]:
    found: dict[str, _Issuer] = {}
    for row in submission.to_dict("records"):
        if str(row[type_column]).strip().upper() != "4":
            continue
        cik = _int(row["ISSUERCIK"])
        ticker = ticker_by_cik.get(cik) if cik is not None else None
        filed = parse_date(str(row["FILING_DATE"]))
        accession = str(row["ACCESSION_NUMBER"]).strip()
        if cik is None or ticker is None or filed is None or not accession:
            continue
        flag = str(row.get("AFF10B5ONE", "")).strip().lower()
        found[accession] = _Issuer(cik=cik, ticker=ticker, filed=filed, flag=flag)
    return found


def _best_owners(frame: pd.DataFrame, accessions: Sequence[str]) -> dict[str, tuple[str, str]]:
    """For each filing, the name and role of its most senior reporting owner."""
    wanted = frame[frame["ACCESSION_NUMBER"].isin(list(accessions))]
    best: dict[str, tuple[int, str, str]] = {}
    for row in wanted.to_dict("records"):
        accession = str(row["ACCESSION_NUMBER"]).strip()
        relationship = str(row.get("RPTOWNER_RELATIONSHIP", "")).upper()
        flags = {
            "officer": "OFFICER" in relationship,
            "director": "DIRECTOR" in relationship,
            "ten_percent": "TENPERCENT" in relationship.replace(" ", "").replace("_", ""),
        }
        rank = role_rank(**flags)
        current = best.get(accession)
        if current is None or rank < current[0]:
            name = str(row.get("RPTOWNERNAME", "")).strip()
            title = str(row.get("RPTOWNER_TITLE", ""))
            best[accession] = (rank, name, role_label(**flags, title=title))
    return {accession: (name, role) for accession, (_, name, role) in best.items()}


def _plan_footnotes(archive: zipfile.ZipFile, accessions: Sequence[str]) -> set[str]:
    """Filings with a footnote that mentions a Rule 10b5-1 plan. The table is optional."""
    if _find_member(archive, "FOOTNOTES") is None:
        return set()
    frame = _read_table(archive, "FOOTNOTES", FOOTNOTE_COLUMNS, required=("ACCESSION_NUMBER",))
    wanted = frame[frame["ACCESSION_NUMBER"].isin(list(accessions))]
    hits: set[str] = set()
    for row in wanted.to_dict("records"):
        if mentions_10b5_1(str(row.get("FOOTNOTE_TXT", ""))):
            hits.add(str(row["ACCESSION_NUMBER"]).strip())
    return hits


def _plan_label(flag: str, accession: str, footnote_hits: set[str]) -> str:
    """The checkbox decides when present; otherwise a footnote mention counts as a plan."""
    if flag in ("1", "true"):
        return "yes"
    if flag in ("0", "false"):
        return "no"
    return "yes" if accession in footnote_hits else "unknown"


def _letter(value: object, allowed: str) -> str:
    letter = str(value).strip().upper() if isinstance(value, str) else ""
    return letter if letter in tuple(allowed) else ""


def read_insider_zip(
    source: str | Path | IO[bytes], ticker_by_cik: Mapping[int, str]
) -> pd.DataFrame:
    """Normalised insider trades from one quarterly SEC data set (a ZIP of TSV tables).

    Only Form 4 filings (not amendments, not Forms 3 or 5) by issuers in `ticker_by_cik` are
    kept, and only non-derivative transactions. The ticker comes from the mapping, not from the
    filing, because filers' own symbols change over time.
    """
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise BulkFormatError("the data set is not a valid ZIP archive") from exc

    with archive:
        submission = _read_table(
            archive,
            "SUBMISSION",
            SUBMISSION_COLUMNS,
            required=("ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK"),
        )
        type_column = next(
            (name for name in ("DOCUMENT_TYPE", "FORM_TYPE") if name in submission.columns), None
        )
        if type_column is None:
            raise BulkFormatError("SUBMISSION.tsv has no document type column")

        issuers = _issuers(submission, type_column, ticker_by_cik)
        if not issuers:
            return empty_trades()
        accessions = list(issuers)

        owners = _best_owners(
            _read_table(archive, "REPORTINGOWNER", OWNER_COLUMNS, required=("ACCESSION_NUMBER",)),
            accessions,
        )
        transactions = _read_table(
            archive, "NONDERIV_TRANS", TRANS_COLUMNS, required=("ACCESSION_NUMBER", "TRANS_CODE")
        )
        transactions = transactions[transactions["ACCESSION_NUMBER"].isin(accessions)]
        footnote_hits = _plan_footnotes(archive, accessions)

    rows: list[dict[str, object]] = []
    for row in transactions.to_dict("records"):
        accession = str(row["ACCESSION_NUMBER"]).strip()
        issuer = issuers.get(accession)
        if issuer is None:
            continue
        name, role = owners.get(accession, ("", ""))
        code = str(row["TRANS_CODE"]).strip().upper()
        rows.append(
            {
                "accession": accession,
                "issuer_cik": issuer.cik,
                "ticker": issuer.ticker,
                "form": "4",
                "filing_date": issuer.filed,
                "trade_date": parse_date(str(row.get("TRANS_DATE", ""))),
                "code": code,
                "kind": classify_transaction(code, False).value,
                "shares": _number(row.get("TRANS_SHARES")),
                "price": _number(row.get("TRANS_PRICEPERSHARE")),
                "acquired": _letter(row.get("TRANS_ACQUIRED_DISP_CD"), "AD"),
                "direct": _letter(row.get("DIRECT_INDIRECT_OWNERSHIP"), "DI"),
                "shares_after": _number(row.get("SHRS_OWND_FOLWNG_TRANS")),
                "owner_name": name,
                "owner_role": role,
                "plan": _plan_label(issuer.flag, accession, footnote_hits),
                "source": "bulk",
            }
        )
    return normalize_trades(pd.DataFrame(rows, columns=TRADE_COLUMNS))
