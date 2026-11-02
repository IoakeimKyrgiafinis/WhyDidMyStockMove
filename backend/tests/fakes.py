from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from whymoved.domain.bars import DailyBars
from whymoved.domain.filings import (
    FilingRef,
    InsiderFiling,
    InsiderTransaction,
    ReportingOwner,
)
from whymoved.domain.news import NewsItem
from whymoved.domain.universe import MARKET_ETF, SECTOR_ETF_TICKERS

FACTORS = [MARKET_ETF, *SECTOR_ETF_TICKERS]
SECTOR_OF = {
    "AAA": "Information Technology",
    "DUP": "Information Technology",
    "BBB": "Financials",
    "CCC": "Energy",
    "DDD": "Health Care",
}


@dataclass
class FakePriceProvider:
    """Answers from an in-memory table and records every request it receives.

    `close` and `volume` are the "truth". Tests can edit them between runs, for example to
    rescale a stock's history the way a split adjustment would. Symbols that are not in the
    table come back as all-NaN columns, like the real adapter.
    """

    close: pd.DataFrame
    volume: pd.DataFrame
    calls: list[tuple[tuple[str, ...], date, date]] = field(default_factory=list)

    def daily_bars(self, symbols: Sequence[str], start: date, end: date) -> DailyBars:
        self.calls.append((tuple(symbols), start, end))
        names = list(symbols)
        window = (self.close.index >= pd.Timestamp(start)) & (self.close.index <= pd.Timestamp(end))
        close = self.close.reindex(columns=names).loc[window].dropna(how="all")
        volume = self.volume.reindex(columns=names).reindex(close.index)
        return DailyBars(close=close.copy(), volume=volume.copy())


@dataclass
class InMemoryStore:
    bars: DailyBars | None = None
    saves: int = 0

    def load(self) -> DailyBars | None:
        if self.bars is None:
            return None
        return DailyBars(close=self.bars.close.copy(), volume=self.bars.volume.copy())

    def save(self, bars: DailyBars) -> None:
        self.bars = DailyBars(close=bars.close.copy(), volume=bars.volume.copy())
        self.saves += 1


def make_world(
    n_days: int = 300, seed: int = 0, extra: Sequence[str] = ()
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Random-walk prices for the factor ETFs and a few stocks.

    DUP is a near-clone of AAA (a second share class). AAA trades ten times the volume, so it
    is the more liquid class.
    """
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2024-01-02", periods=n_days, name="date")

    returns: dict[str, np.ndarray] = {}
    for symbol in [*FACTORS, "AAA", "BBB", "CCC", "DDD", *extra]:
        returns[symbol] = rng.normal(0.0003, 0.01, n_days)
    returns["DUP"] = returns["AAA"] + rng.normal(0.0, 0.0005, n_days)

    prices = {
        symbol: (50.0 + 10.0 * k) * np.exp(np.cumsum(series))
        for k, (symbol, series) in enumerate(returns.items())
    }
    close = pd.DataFrame(prices, index=index)
    volume = pd.DataFrame({s: np.full(n_days, 1_000_000.0) for s in returns}, index=index)
    volume["AAA"] = 10_000_000.0
    return close, volume


def holdings_csv(sectors: dict[str, str]) -> str:
    """An iShares-style holdings file. Weights fall in the order the stocks are given."""
    lines = [
        "iShares Test ETF",
        "Fund Holdings as of,Jan 1 2025",
        "",
        "Ticker,Name,Sector,Asset Class,Market Value,Weight (%)",
    ]
    for rank, (ticker, sector) in enumerate(sectors.items()):
        weight = 10.0 - rank
        lines.append(f'"{ticker}","{ticker} INC","{sector}","Equity","1,000.00","{weight}"')
    lines.append('"The content contained herein is owned or licensed by the fund sponsor."')
    return "\n".join(lines) + "\n"


@dataclass
class FakeNewsProvider:
    """Answers from an in-memory list and records every request it receives."""

    items: list[NewsItem]
    calls: list[tuple[tuple[str, ...], datetime, datetime]] = field(default_factory=list)

    def news(self, symbols: Sequence[str], start: datetime, end: datetime) -> list[NewsItem]:
        self.calls.append((tuple(symbols), start, end))
        wanted = set(symbols)
        return [
            item
            for item in self.items
            if start <= item.created_at <= end and wanted & set(item.symbols)
        ]


@dataclass
class InMemoryNewsStore:
    items: list[NewsItem] = field(default_factory=list)
    saves: int = 0

    def load(self) -> list[NewsItem]:
        return list(self.items)

    def save(self, items: Sequence[NewsItem]) -> None:
        self.items = list(items)
        self.saves += 1


def make_ref(
    accession: str,
    form: str = "4",
    filed: date = date(2026, 8, 28),
    *,
    cik: int = 3453,
    items: tuple[str, ...] = (),
) -> FilingRef:
    return FilingRef(
        cik=cik,
        accession=accession,
        form=form,
        filing_date=filed,
        accepted_at=None,
        report_date=None,
        primary_document=f"{accession}.xml",
        items=items,
    )


def make_insider_tx(
    code: str = "S",
    *,
    derivative: bool = False,
    shares: float | None = 100.0,
    price: float | None = 10.0,
    acquired: bool | None = False,
    direct: bool | None = True,
    when: date | None = date(2026, 8, 27),
    after: float | None = 500.0,
) -> InsiderTransaction:
    return InsiderTransaction(
        security_title="Common Stock",
        transaction_date=when,
        code=code,
        acquired=acquired,
        shares=shares,
        price=price,
        shares_after=after,
        direct=direct,
        is_derivative=derivative,
    )


def make_owner(
    name: str = "Doe Jane",
    *,
    officer: bool = True,
    director: bool = False,
    ten_percent: bool = False,
    title: str = "CEO",
) -> ReportingOwner:
    return ReportingOwner(
        cik=1,
        name=name,
        is_director=director,
        is_officer=officer,
        is_ten_percent_owner=ten_percent,
        is_other=False,
        officer_title=title,
    )


def make_insider_filing(
    ref: FilingRef,
    transactions: Sequence[InsiderTransaction] | None = None,
    *,
    owners: Sequence[ReportingOwner] | None = None,
    plan_flag: bool | None = None,
    footnote: bool = False,
) -> InsiderFiling:
    return InsiderFiling(
        ref=ref,
        issuer_cik=ref.cik,
        issuer_name="ACME CORP",
        issuer_ticker="ACME",
        period_of_report=None,
        schema_version="X0508",
        owners=tuple(owners if owners is not None else [make_owner()]),
        transactions=tuple(transactions if transactions is not None else [make_insider_tx()]),
        plan_flag=plan_flag,
        footnote_mentions_plan=footnote,
        footnotes=(),
    )


@dataclass
class FakeFilings:
    """Answers from in-memory tables and records every request it receives."""

    refs: dict[int, list[FilingRef]] = field(default_factory=dict)
    filings: dict[str, InsiderFiling] = field(default_factory=dict)
    failures: dict[str, Exception] = field(default_factory=dict)
    list_failures: dict[int, Exception] = field(default_factory=dict)
    list_calls: list[tuple[int, frozenset[str], date]] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)

    def ticker_map(self) -> dict[str, int]:
        return {}

    def recent_filings(self, cik: int, forms: Collection[str], since: date) -> list[FilingRef]:
        self.list_calls.append((cik, frozenset(forms), since))
        if cik in self.list_failures:
            raise self.list_failures[cik]
        return [r for r in self.refs.get(cik, []) if r.form in forms and r.filing_date >= since]

    def insider_filing(self, ref: FilingRef) -> InsiderFiling:
        self.fetched.append(ref.accession)
        if ref.accession in self.failures:
            raise self.failures[ref.accession]
        return self.filings[ref.accession]
