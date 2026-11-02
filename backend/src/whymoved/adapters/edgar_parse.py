from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as SafeET

from whymoved.adapters.edgar_errors import EdgarFormatError
from whymoved.domain.filings import (
    FilingRef,
    InsiderFiling,
    InsiderTransaction,
    ReportingOwner,
    mentions_10b5_1,
)
from whymoved.domain.universe import normalize_symbol


@dataclass(frozen=True)
class SubmissionPage:
    """An older page of a company's filing list, listed in the main submissions response."""

    name: str
    filing_from: date
    filing_to: date


def as_mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise EdgarFormatError(f"unexpected {what} in the response")
    return value


def _as_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _date(text: str | None) -> date | None:
    if text is None or len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _accepted(value: object) -> datetime | None:
    text = _as_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _items(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def _column(columns: Mapping[str, object], name: str) -> list[object] | None:
    value = columns.get(name)
    if value is None:
        return None
    if not isinstance(value, list):
        raise EdgarFormatError(f"column {name!r} is not a list")
    return value


def parse_filing_columns(columns: Mapping[str, object], cik: int) -> list[FilingRef]:
    """Turn EDGAR's columnar filing list (parallel arrays) into filing references."""
    accession = _column(columns, "accessionNumber")
    filing_dates = _column(columns, "filingDate")
    forms = _column(columns, "form")
    documents = _column(columns, "primaryDocument")
    if accession is None or filing_dates is None or forms is None or documents is None:
        raise EdgarFormatError("the filing list is missing a required column")

    n = len(accession)
    report = _column(columns, "reportDate")
    accepted = _column(columns, "acceptanceDateTime")
    items = _column(columns, "items")
    others = (
        ("filingDate", filing_dates),
        ("form", forms),
        ("primaryDocument", documents),
        ("reportDate", report),
        ("acceptanceDateTime", accepted),
        ("items", items),
    )
    for name, column in others:
        if column is not None and len(column) != n:
            raise EdgarFormatError(f"column {name!r} has a different length")

    refs: list[FilingRef] = []
    for i in range(n):
        number = _as_text(accession[i])
        form = _as_text(forms[i])
        filed = _date(_as_text(filing_dates[i]))
        if number is None or form is None or filed is None:
            raise EdgarFormatError(f"filing {i} has no usable accession number, form or date")
        refs.append(
            FilingRef(
                cik=cik,
                accession=number,
                form=form,
                filing_date=filed,
                accepted_at=_accepted(accepted[i]) if accepted is not None else None,
                report_date=_date(_as_text(report[i])) if report is not None else None,
                primary_document=_as_text(documents[i]) or "",
                items=_items(items[i]) if items is not None else (),
            )
        )
    return refs


def parse_submissions(payload: object, cik: int) -> tuple[list[FilingRef], list[SubmissionPage]]:
    """The recent filings and the list of older pages from a company's submissions response."""
    root = as_mapping(payload, "submissions response")
    filings = as_mapping(root.get("filings"), "filings section")
    refs = parse_filing_columns(as_mapping(filings.get("recent"), "recent filings"), cik)

    listed = filings.get("files") or []
    if not isinstance(listed, list):
        raise EdgarFormatError("the list of older pages is not a list")
    pages: list[SubmissionPage] = []
    for entry in listed:
        page = as_mapping(entry, "older page entry")
        name = _as_text(page.get("name"))
        start = _date(_as_text(page.get("filingFrom")))
        end = _date(_as_text(page.get("filingTo")))
        if name is None or start is None or end is None:
            raise EdgarFormatError("an older page entry has no name or date range")
        pages.append(SubmissionPage(name=name, filing_from=start, filing_to=end))
    return refs, pages


def parse_company_tickers(payload: object) -> dict[str, int]:
    """Ticker to CIK from the SEC's company tickers file. Unusable entries are skipped."""
    data = as_mapping(payload, "company tickers file")
    result: dict[str, int] = {}
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        ticker = entry.get("ticker")
        raw_cik = entry.get("cik_str")
        if not isinstance(ticker, str):
            continue
        if isinstance(raw_cik, int) and not isinstance(raw_cik, bool):
            cik = raw_cik
        elif isinstance(raw_cik, str) and raw_cik.isdigit():
            cik = int(raw_cik)
        else:
            continue
        symbol = normalize_symbol(ticker)
        if symbol is not None:
            result.setdefault(symbol, cik)
    return result


def raw_xml_document(primary_document: str) -> str:
    """The raw XML file name for a Form 4's primaryDocument.

    EDGAR often lists a rendered view under an 'xsl...' folder (for example
    'xslF345X05/form4.xml'); the raw XML has the same name one level up.
    """
    parts = primary_document.split("/")
    if len(parts) > 1 and parts[0].lower().startswith("xsl"):
        return "/".join(parts[1:])
    return primary_document


# ---- Form 4 XML ----


def _text(node: Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path)
    if found is None or found.text is None:
        return None
    value = found.text.strip()
    return value or None


def _flag(text: str | None) -> bool | None:
    if text is None:
        return None
    lowered = text.lower()
    if lowered in ("1", "true"):
        return True
    if lowered in ("0", "false"):
        return False
    return None


def _number(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        value = float(text.replace(",", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _acquired(text: str | None) -> bool | None:
    letter = (text or "").upper()
    if letter == "A":
        return True
    if letter == "D":
        return False
    return None


def _direct(text: str | None) -> bool | None:
    letter = (text or "").upper()
    if letter == "D":
        return True
    if letter == "I":
        return False
    return None


def _owner(node: Element) -> ReportingOwner:
    relationship = node.find("reportingOwnerRelationship")
    return ReportingOwner(
        cik=_int(_text(node, "reportingOwnerId/rptOwnerCik")),
        name=_text(node, "reportingOwnerId/rptOwnerName") or "",
        is_director=_flag(_text(relationship, "isDirector")) is True,
        is_officer=_flag(_text(relationship, "isOfficer")) is True,
        is_ten_percent_owner=_flag(_text(relationship, "isTenPercentOwner")) is True,
        is_other=_flag(_text(relationship, "isOther")) is True,
        officer_title=_text(relationship, "officerTitle") or "",
    )


def _transaction(node: Element, is_derivative: bool) -> InsiderTransaction:
    return InsiderTransaction(
        security_title=_text(node, "securityTitle/value") or "",
        transaction_date=_date(_text(node, "transactionDate/value")),
        code=(_text(node, "transactionCoding/transactionCode") or "").upper(),
        acquired=_acquired(_text(node, "transactionAmounts/transactionAcquiredDisposedCode/value")),
        shares=_number(_text(node, "transactionAmounts/transactionShares/value")),
        price=_number(_text(node, "transactionAmounts/transactionPricePerShare/value")),
        shares_after=_number(
            _text(node, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
        ),
        direct=_direct(_text(node, "ownershipNature/directOrIndirectOwnership/value")),
        is_derivative=is_derivative,
    )


def parse_form4(document: bytes | str, ref: FilingRef) -> InsiderFiling:
    """Parse the raw XML of a Form 4 (or 4/A).

    Parsing refuses entity expansion and external references. Optional fields that are
    missing or malformed become None; a document that is not a valid ownershipDocument
    raises EdgarFormatError.
    """
    try:
        root = SafeET.fromstring(document)
    except (ParseError, ValueError) as exc:
        raise EdgarFormatError(f"the Form 4 document could not be parsed: {exc}") from exc
    if root.tag != "ownershipDocument":
        raise EdgarFormatError("the document is not an ownershipDocument")

    footnotes = tuple(
        (note.text or "").strip() for note in root.findall("footnotes/footnote") if note.text
    )
    transactions = [
        _transaction(node, False)
        for node in root.findall("nonDerivativeTable/nonDerivativeTransaction")
    ] + [_transaction(node, True) for node in root.findall("derivativeTable/derivativeTransaction")]

    return InsiderFiling(
        ref=ref,
        issuer_cik=_int(_text(root, "issuer/issuerCik")),
        issuer_name=_text(root, "issuer/issuerName") or "",
        issuer_ticker=_text(root, "issuer/issuerTradingSymbol") or "",
        period_of_report=_date(_text(root, "periodOfReport")),
        schema_version=_text(root, "schemaVersion") or "",
        owners=tuple(_owner(node) for node in root.findall("reportingOwner")),
        transactions=tuple(transactions),
        plan_flag=_flag(_text(root, "aff10b5One")),
        footnote_mentions_plan=any(mentions_10b5_1(note) for note in footnotes),
        footnotes=footnotes,
    )
