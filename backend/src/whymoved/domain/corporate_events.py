from __future__ import annotations

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
