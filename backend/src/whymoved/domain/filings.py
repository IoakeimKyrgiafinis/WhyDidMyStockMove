from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum


class FilingNotFound(Exception):
    """A requested filing or company does not exist."""


class FilingFormatError(Exception):
    """A filing does not have the expected structure."""


_PLAN = re.compile(r"10b5\W*1", re.IGNORECASE)

# My reading of the Form 4 transaction codes; to be checked against the SEC's code list.
# Awards, option exercises and conversions, tax withholding, gifts and similar carry
# little information about an insider's view of the stock.
ROUTINE_CODES = frozenset("ADFMXCEHOGWLZU")


class TransactionKind(StrEnum):
    OPEN_MARKET_BUY = "open_market_buy"
    OPEN_MARKET_SELL = "open_market_sell"
    ROUTINE = "routine"
    OTHER = "other"


def classify_transaction(code: str, is_derivative: bool) -> TransactionKind:
    """Only open-market purchases and sales of the stock itself count as buys and sells."""
    letter = code.strip().upper()
    if not is_derivative and letter == "P":
        return TransactionKind.OPEN_MARKET_BUY
    if not is_derivative and letter == "S":
        return TransactionKind.OPEN_MARKET_SELL
    if letter in ROUTINE_CODES:
        return TransactionKind.ROUTINE
    return TransactionKind.OTHER


EARLIEST_DATE = date(1990, 1, 1)
LATEST_DATE = date(2100, 1, 1)


def plausible_date(value: date | None) -> date | None:
    """The date, or None if it cannot be real. Filings contain typos such as year 0024, which
    Python accepts but pandas cannot store; one such row must not stop an import."""
    if value is None or not EARLIEST_DATE <= value < LATEST_DATE:
        return None
    return value


def mentions_10b5_1(text: str) -> bool:
    """True if a footnote refers to a Rule 10b5-1 trading plan (any hyphen style)."""
    return _PLAN.search(text) is not None


def business_days_between(start: date, end: date) -> int:
    """Weekdays after `start` up to and including `end`. Market holidays are not excluded."""
    if end < start:
        raise ValueError("end must not be before start")
    full_weeks, rest = divmod((end - start).days, 7)
    count = full_weeks * 5
    for k in range(1, rest + 1):
        if (start + timedelta(days=k)).weekday() < 5:
            count += 1
    return count


@dataclass(frozen=True)
class FilingRef:
    cik: int
    accession: str
    form: str  # "4", "4/A", "8-K", ...
    filing_date: date
    accepted_at: datetime | None  # EDGAR acceptance time, naive UTC
    report_date: date | None
    primary_document: str
    items: tuple[str, ...] = ()  # 8-K item numbers, e.g. ("2.02", "9.01")


@dataclass(frozen=True)
class ReportingOwner:
    cik: int | None
    name: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    is_other: bool
    officer_title: str

    @property
    def role(self) -> str:
        if self.is_officer:
            return self.officer_title or "Officer"
        if self.is_director:
            return "Director"
        if self.is_ten_percent_owner:
            return "10% owner"
        return "Other"


@dataclass(frozen=True)
class InsiderTransaction:
    security_title: str
    transaction_date: date | None
    code: str
    acquired: bool | None  # True if acquired, False if disposed
    shares: float | None
    price: float | None
    shares_after: float | None
    direct: bool | None  # True for direct ownership, False for indirect
    is_derivative: bool

    @property
    def kind(self) -> TransactionKind:
        return classify_transaction(self.code, self.is_derivative)

    @property
    def value(self) -> float | None:
        if self.shares is None or self.price is None or self.price <= 0.0:
            return None
        return self.shares * self.price


@dataclass(frozen=True)
class InsiderFiling:
    ref: FilingRef
    issuer_cik: int | None
    issuer_name: str
    issuer_ticker: str
    period_of_report: date | None
    schema_version: str
    owners: tuple[ReportingOwner, ...]
    transactions: tuple[InsiderTransaction, ...]
    plan_flag: bool | None  # the 10b5-1 checkbox; None on older filings that lack it
    footnote_mentions_plan: bool
    footnotes: tuple[str, ...]

    @property
    def under_plan(self) -> bool:
        """Whether the trades were made under a 10b5-1 plan.

        The checkbox decides when it is present. Older filings have no checkbox, so a
        footnote that mentions a plan is used instead.
        """
        if self.plan_flag is not None:
            return self.plan_flag
        return self.footnote_mentions_plan

    def filing_lag(self, transaction: InsiderTransaction) -> int | None:
        """Business days from the trade to the filing (the SEC allows two)."""
        traded = transaction.transaction_date
        if traded is None or self.ref.filing_date < traded:
            return None
        return business_days_between(traded, self.ref.filing_date)
