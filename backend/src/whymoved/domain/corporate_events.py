from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from whymoved.domain.filings import FilingRef

EVENT_COLUMNS = [
    "ticker",
    "cik",
    "accession",
    "form",
    "filing_date",
    "accepted_at",
    "items",
    "primary_document",
]

# The standard Form 8-K item list, from memory; unknown items fall back to "Item X.XX".
ITEM_LABELS = {
    "1.01": "Entry into a material definitive agreement",
    "1.02": "Termination of a material definitive agreement",
    "1.03": "Bankruptcy or receivership",
    "1.05": "Material cybersecurity incident",
    "2.01": "Completion of an acquisition or disposition of assets",
    "2.02": "Results of operations and financial condition (earnings)",
    "2.03": "Creation of a direct financial obligation",
    "2.04": "Events that accelerate or increase a financial obligation",
    "2.05": "Costs of exit or disposal activities",
    "2.06": "Material impairments",
    "3.01": "Notice of delisting or failure to meet a listing rule",
    "3.02": "Unregistered sales of equity securities",
    "3.03": "Material modification to the rights of security holders",
    "4.01": "Change in certifying accountant",
    "4.02": "Non-reliance on previously issued financial statements",
    "5.01": "Change in control of the company",
    "5.02": "Departure or appointment of directors or officers",
    "5.03": "Amendment to articles or bylaws; change in fiscal year",
    "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
    "9.01": "Financial statements and exhibits",
}


FormCategory = Literal["current", "ownership", "offering", "tender", "merger", "report"]


@dataclass(frozen=True)
class FormKind:
    """How a filing type is shown. `minor` filings are listed but get no chart marker: they are
    routine (a passive holder's annual 13G, a scheduled quarterly report)."""

    category: FormCategory
    label: str
    minor: bool = False


_ACTIVIST = FormKind("ownership", "Activist or large stake (13D)")
_PASSIVE = FormKind("ownership", "Passive large holder (13G)", minor=True)
_REGISTRATION = FormKind("offering", "Registration of a share sale")
_SHELF = FormKind("offering", "Shelf registration", minor=True)
_PROSPECTUS = FormKind("offering", "Offering prospectus")

# Company filings collected alongside 8-Ks. Schedules 13D and 13G changed their EDGAR form names
# in 2025 ("SC 13D" became "SCHEDULE 13D"), so both spellings are listed. 424B2, 424B3 and 424B8
# are left out on purpose: banks file tens of thousands of them for structured notes.
FORM_KINDS: dict[str, FormKind] = {
    "8-K": FormKind("current", "8-K"),
    "6-K": FormKind("current", "Foreign issuer report (6-K)"),
    "SCHEDULE 13D": _ACTIVIST,
    "SCHEDULE 13D/A": _ACTIVIST,
    "SC 13D": _ACTIVIST,
    "SC 13D/A": _ACTIVIST,
    "SCHEDULE 13G": _PASSIVE,
    "SCHEDULE 13G/A": _PASSIVE,
    "SC 13G": _PASSIVE,
    "SC 13G/A": _PASSIVE,
    "S-1": _REGISTRATION,
    "F-1": _REGISTRATION,
    "S-3": _SHELF,
    "S-3ASR": _SHELF,
    "F-3": _SHELF,
    "F-3ASR": _SHELF,
    "424B1": _PROSPECTUS,
    "424B4": _PROSPECTUS,
    "424B5": _PROSPECTUS,
    "SC TO-T": FormKind("tender", "Tender offer for the shares"),
    "SC TO-I": FormKind("tender", "Company tender offer (buyback)"),
    "SC 14D9": FormKind("tender", "Board response to a tender offer"),
    "DEFM14A": FormKind("merger", "Merger vote proxy"),
    "10-Q": FormKind("report", "Quarterly report (10-Q)", minor=True),
    "10-K": FormKind("report", "Annual report (10-K)", minor=True),
    "20-F": FormKind("report", "Annual report (20-F)", minor=True),
    "40-F": FormKind("report", "Annual report (40-F)", minor=True),
}
EVENT_FORMS = frozenset(FORM_KINDS)


def form_kind(form: str) -> FormKind:
    """The display kind of a form type; an unknown one is a plain current report."""
    return FORM_KINDS.get(form.strip(), FormKind("current", form.strip() or "Filing"))


def is_own_stake_report(form: str, accession: str, cik: int) -> bool:
    """True for a 13D or 13G the company submitted itself. A company's filing list holds the
    schedules about it and also those it files as a holder of another company's shares (a bank
    files thousands). The first ten digits of an accession number are the submitter's CIK, so a
    schedule submitted under the company's own CIK is about someone else."""
    prefix = accession.split("-", 1)[0]
    return form_kind(form).category == "ownership" and prefix.isdigit() and int(prefix) == cik


def item_label(item: str) -> str:
    code = item.strip()
    return ITEM_LABELS.get(code, f"Item {code}")


def is_earnings(items: str) -> bool:
    """True if a comma-separated list of 8-K items includes Item 2.02 (results of operations)."""
    return "2.02" in [part.strip() for part in items.split(",")]


def event_row(ticker: str, ref: FilingRef) -> dict[str, object]:
    return {
        "ticker": ticker,
        "cik": ref.cik,
        "accession": ref.accession,
        "form": ref.form,
        "filing_date": ref.filing_date,
        "accepted_at": ref.accepted_at,
        "items": ",".join(ref.items),
        "primary_document": ref.primary_document,
    }


def normalize_events(frame: pd.DataFrame) -> pd.DataFrame:
    """Enforce the events schema: column order and dtypes. A missing column is an error."""
    missing = [name for name in EVENT_COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(f"events are missing columns: {missing}")
    data = frame[EVENT_COLUMNS].reset_index(drop=True)
    columns: dict[str, pd.Series] = {}
    for name in EVENT_COLUMNS:
        if name == "cik":
            columns[name] = pd.to_numeric(data[name]).astype("int64")
        elif name in ("filing_date", "accepted_at"):
            columns[name] = pd.to_datetime(data[name]).dt.as_unit("ns")
        else:
            columns[name] = data[name].astype("object")
    return pd.DataFrame(columns)[EVENT_COLUMNS]


def empty_events() -> pd.DataFrame:
    return normalize_events(pd.DataFrame({name: [] for name in EVENT_COLUMNS}))
