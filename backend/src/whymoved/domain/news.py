from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NewsItem:
    """A headline tagged to one or more tickers.

    Only the headline, source, link and timestamps are kept. Article text and summaries are
    not stored: providers' licences usually do not allow republishing them, and the site links
    to the original instead.
    """

    id: int
    headline: str
    source: str
    url: str
    created_at: datetime  # naive UTC
    updated_at: datetime | None  # naive UTC
    symbols: tuple[str, ...]

    @property
    def last_touched(self) -> datetime:
        return self.updated_at or self.created_at
