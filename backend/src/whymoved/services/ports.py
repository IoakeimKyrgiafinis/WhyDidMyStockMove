from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date, datetime
from typing import Protocol

import pandas as pd

from whymoved.domain.bars import DailyBars
from whymoved.domain.filings import FilingRef, InsiderFiling
from whymoved.domain.news import NewsItem


class PriceProvider(Protocol):
    """Source of daily price history. Implemented by the Alpaca adapter and by test fakes."""

    def daily_bars(self, symbols: Sequence[str], start: date, end: date) -> DailyBars:
        """Daily bars for `symbols` from `start` to `end`, both inclusive."""
        ...


class PriceStore(Protocol):
    """Where the price history is kept between runs."""

    def load(self) -> DailyBars | None:
        """The stored history, or None if nothing has been stored yet."""
        ...

    def save(self, bars: DailyBars) -> None:
        """Replace the stored history."""
        ...


class FilingsProvider(Protocol):
    """Source of SEC filings. Implemented by the EDGAR client and by test fakes."""

    def ticker_map(self) -> dict[str, int]:
        """Ticker to CIK."""
        ...

    def recent_filings(self, cik: int, forms: Collection[str], since: date) -> list[FilingRef]:
        """Filings of the given form types filed on or after `since`, oldest first."""
        ...

    def insider_filing(self, ref: FilingRef) -> InsiderFiling:
        """The parsed contents of one Form 4."""
        ...


class NewsProvider(Protocol):
    """Source of headlines. Implemented by the Alpaca adapter and by test fakes."""

    def news(self, symbols: Sequence[str], start: datetime, end: datetime) -> list[NewsItem]:
        """Headlines tagged to any of `symbols` between two naive-UTC datetimes."""
        ...


class NewsStore(Protocol):
    """Where headlines are kept between runs."""

    def load(self) -> list[NewsItem]:
        """The stored headlines, oldest first (empty if nothing has been stored yet)."""
        ...

    def save(self, items: Sequence[NewsItem]) -> None:
        """Replace the stored headlines."""
        ...


class InsiderTradeSource(Protocol):
    """Source of normalised insider trades, one quarter at a time."""

    def trades(self, year: int, quarter: int) -> pd.DataFrame | None:
        """The quarter's trades, or None if it is not published (yet)."""
        ...


class IntradayProvider(Protocol):
    """Source of intraday bar closes (wide frame, naive UTC bar-start index)."""

    def bars(self, symbols: Sequence[str], start: datetime, end: datetime) -> pd.DataFrame: ...
