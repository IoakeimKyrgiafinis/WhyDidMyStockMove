from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from whymoved.domain.news import NewsItem
from whymoved.services.ports import NewsProvider, NewsStore

logger = logging.getLogger(__name__)

DEFAULT_OVERLAP = timedelta(days=2)


@dataclass(frozen=True)
class NewsReport:
    n_total: int  # headlines stored after this run
    n_fetched: int  # headlines returned by the provider
    n_new: int  # fetched headlines that were not stored before
    start: datetime
    end: datetime
    forced: bool


def merge_news(
    existing: Sequence[NewsItem], fresh: Sequence[NewsItem], cutoff: datetime | None = None
) -> list[NewsItem]:
    """Combine stored and freshly fetched headlines.

    A fresh headline replaces the stored one with the same id unless it is older (by last
    update). Headlines created before `cutoff` are dropped. The result is sorted by creation
    time, then id.
    """
    by_id: dict[int, NewsItem] = {item.id: item for item in existing}
    for item in fresh:
        current = by_id.get(item.id)
        if current is None or item.last_touched >= current.last_touched:
            by_id[item.id] = item
    kept = [item for item in by_id.values() if cutoff is None or item.created_at >= cutoff]
    return sorted(kept, key=lambda item: (item.created_at, item.id))


def collect_news(
    provider: NewsProvider,
    store: NewsStore,
    symbols: Sequence[str],
    *,
    history_start: datetime,
    end: datetime,
    overlap: timedelta = DEFAULT_OVERLAP,
    force_full: bool = False,
) -> tuple[list[NewsItem], NewsReport]:
    """Bring the stored headlines up to `end` and save them.

    Stored headlines older than `history_start` are dropped, so the file stays small. Normal
    runs fetch from the newest stored headline minus `overlap` (updated articles and late tags
    are picked up and deduplicated by id). With `force_full`, stored headlines are ignored and
    everything since `history_start` is fetched again.
    """
    names = list(symbols)
    if not names:
        raise ValueError("symbols must not be empty")
    if len(set(names)) != len(names):
        raise ValueError("symbols must be unique")
    if history_start >= end:
        raise ValueError("history_start must be before end")
    if overlap <= timedelta(0):
        raise ValueError("overlap must be positive")

    existing = [] if force_full else store.load()
    start = history_start
    if existing:
        newest = max(item.created_at for item in existing)
        start = max(history_start, newest - overlap)

    fresh: list[NewsItem] = []
    if start < end:
        fresh = provider.news(names, start, end)

    known = {item.id for item in existing}
    merged = merge_news(existing, fresh, cutoff=history_start)
    store.save(merged)

    report = NewsReport(
        n_total=len(merged),
        n_fetched=len(fresh),
        n_new=sum(1 for item in fresh if item.id not in known),
        start=start,
        end=end,
        forced=force_full,
    )
    logger.info(
        "news updated: %d stored, %d fetched, %d new",
        report.n_total,
        report.n_fetched,
        report.n_new,
    )
    return merged, report
