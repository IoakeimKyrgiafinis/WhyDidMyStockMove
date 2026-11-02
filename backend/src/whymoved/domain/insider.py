from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date

import pandas as pd

from whymoved.domain.filings import InsiderFiling, ReportingOwner, classify_transaction

TRADE_COLUMNS = [
    "accession",
    "issuer_cik",
    "ticker",
    "form",
    "filing_date",
    "trade_date",
    "code",
    "kind",
    "shares",
    "price",
    "acquired",
    "direct",
    "shares_after",
    "owner_name",
    "owner_role",
    "plan",
    "source",
]

_TEXT_COLUMNS = (
    "accession",
    "ticker",
    "form",
    "code",
    "kind",
    "acquired",
    "direct",
    "owner_name",
    "owner_role",
    "plan",
    "source",
)
_FLOAT_COLUMNS = ("shares", "price", "shares_after")


def normalize_trades(frame: pd.DataFrame) -> pd.DataFrame:
    """Enforce the trades schema: column order and dtypes. A missing column is an error."""
    missing = [name for name in TRADE_COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(f"trades are missing columns: {missing}")

    data = frame[TRADE_COLUMNS].reset_index(drop=True)
    columns: dict[str, pd.Series] = {}
    for name in TRADE_COLUMNS:
        if name in _TEXT_COLUMNS:
            columns[name] = data[name].astype("object")
        elif name in _FLOAT_COLUMNS:
            columns[name] = pd.to_numeric(data[name], errors="coerce").astype("float64")
        elif name == "issuer_cik":
            columns[name] = pd.to_numeric(data[name]).astype("int64")
        else:
            columns[name] = pd.to_datetime(data[name]).dt.as_unit("ns")
    return pd.DataFrame(columns)[TRADE_COLUMNS]


def empty_trades() -> pd.DataFrame:
    return normalize_trades(pd.DataFrame({name: [] for name in TRADE_COLUMNS}))


def role_label(*, officer: bool, director: bool, ten_percent: bool, title: str = "") -> str:
    if officer:
        return title.strip() or "Officer"
    if director:
        return "Director"
    if ten_percent:
        return "10% owner"
    return "Other"


def role_rank(*, officer: bool, director: bool, ten_percent: bool) -> int:
    """Lower is more senior. Used to pick one owner when a filing has several."""
    if officer:
        return 0
    if director:
        return 1
    if ten_percent:
        return 2
    return 3


def quarter_key(year: int, quarter: int) -> str:
    return f"{year}q{quarter}"


def _quarter_of(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


def quarters_between(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, quarter) from the quarter containing `start` to the one containing `end`."""
    if end < start:
        raise ValueError("end must not be before start")
    year, quarter = _quarter_of(start)
    last = _quarter_of(end)
    out: list[tuple[int, int]] = []
    while (year, quarter) <= last:
        out.append((year, quarter))
        quarter += 1
        if quarter == 5:
            year, quarter = year + 1, 1
    return out


def plan_quarters(
    history_start: date,
    today: date,
    imported: Collection[str],
    refresh_latest: int = 2,
) -> list[tuple[int, int]]:
    """Quarters to download: every quarter not imported yet, plus the latest `refresh_latest`.

    The newest quarters are always refreshed: filings made late in a quarter roll into the next
    release, and the newest file may be posted before the quarter is complete.
    """
    if refresh_latest < 0:
        raise ValueError("refresh_latest must be non-negative")
    wanted = quarters_between(history_start, today)
    done = set(imported)
    refresh = set(wanted[-refresh_latest:]) if refresh_latest else set()
    return [q for q in wanted if q in refresh or quarter_key(*q) not in done]


def plan_label(filing: InsiderFiling) -> str:
    """'yes' or 'no' when the 10b5-1 checkbox is present, else 'yes' if a footnote mentions a
    plan, else 'unknown'. Matches how the bulk importer labels filings."""
    if filing.plan_flag is True:
        return "yes"
    if filing.plan_flag is False:
        return "no"
    return "yes" if filing.footnote_mentions_plan else "unknown"


def primary_owner(owners: Sequence[ReportingOwner]) -> ReportingOwner | None:
    """The most senior reporting owner (officer, director, 10% owner, other); first on ties."""
    best: ReportingOwner | None = None
    best_rank = 99
    for owner in owners:
        rank = role_rank(
            officer=owner.is_officer,
            director=owner.is_director,
            ten_percent=owner.is_ten_percent_owner,
        )
        if rank < best_rank:
            best, best_rank = owner, rank
    return best


def trades_from_filing(filing: InsiderFiling, ticker: str) -> pd.DataFrame:
    """Normalised rows for the non-derivative transactions of a Form 4.

    Amendments (4/A) give no rows, and neither does a filing with only derivative transactions.
    The ticker is the universe ticker we looked the company up by.
    """
    if filing.ref.form != "4":
        return empty_trades()
    owner = primary_owner(filing.owners)
    rows: list[dict[str, object]] = []
    for tx in filing.transactions:
        if tx.is_derivative:
            continue
        rows.append(
            {
                "accession": filing.ref.accession,
                "issuer_cik": filing.ref.cik,
                "ticker": ticker,
                "form": "4",
                "filing_date": filing.ref.filing_date,
                "trade_date": tx.transaction_date,
                "code": tx.code,
                "kind": classify_transaction(tx.code, False).value,
                "shares": tx.shares,
                "price": tx.price,
                "acquired": "" if tx.acquired is None else ("A" if tx.acquired else "D"),
                "direct": "" if tx.direct is None else ("D" if tx.direct else "I"),
                "shares_after": tx.shares_after,
                "owner_name": owner.name if owner else "",
                "owner_role": owner.role if owner else "",
                "plan": plan_label(filing),
                "source": "xml",
            }
        )
    return normalize_trades(pd.DataFrame(rows, columns=TRADE_COLUMNS))
