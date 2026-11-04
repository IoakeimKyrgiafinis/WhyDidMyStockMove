"""Read GDELT's 15-minute files and keep the articles about universe companies."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import pandas as pd

from whymoved.adapters.gdelt import GkgArticle
from whymoved.adapters.json_http import SourceError
from whymoved.domain.world_news import (
    WORLD_COLUMNS,
    Matcher,
    clean_title,
    empty_world,
    is_noise,
    normalize_world,
)

logger = logging.getLogger(__name__)


class GkgSource(Protocol):
    def articles(self, stamp: datetime) -> list[GkgArticle] | None: ...


@dataclass(frozen=True)
class WorldReport:
    n_files: int
    n_missing: int
    n_articles: int  # articles read
    n_kept: int  # article-company pairs kept
    through: datetime | None  # the last file read
    error: str | None = None  # why the run stopped early, if a file could not be fetched


def collect_world_news(
    source: GkgSource,
    matcher: Matcher,
    file_stamps: Sequence[datetime],
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[pd.DataFrame, WorldReport]:
    """Read the files in order; stop early at `deadline`, reporting how far it got so the next
    run carries on from there."""
    rows: list[dict[str, object]] = []
    n_files = n_missing = n_articles = 0
    through: datetime | None = None
    error: str | None = None
    for stamp in file_stamps:
        if deadline is not None and clock() >= deadline:
            logger.info("time is up after %d files; the rest is left for the next run", n_files)
            break
        try:
            articles = source.articles(stamp)
        except SourceError as exc:
            # Keep what was read; the next run starts again from this file.
            error = str(exc)
            logger.warning("stopping at %s: %s", stamp.isoformat(), exc)
            break
        n_files += 1
        through = stamp
        if articles is None:
            n_missing += 1
            continue
        n_articles += len(articles)
        for a in articles:
            title = clean_title(a.title)
            if not title or is_noise(a.domain, title):
                continue
            for ticker in sorted(matcher.tickers_for(title, a.organisations)):
                rows.append(
                    {
                        "ticker": ticker,
                        "seen_at": a.seen_at,
                        "title": title,
                        "url": a.url,
                        "domain": a.domain,
                        "tone": a.tone,
                    }
                )
    frame = normalize_world(pd.DataFrame(rows, columns=WORLD_COLUMNS)) if rows else empty_world()
    return frame, WorldReport(
        n_files=n_files,
        n_missing=n_missing,
        n_articles=n_articles,
        n_kept=len(frame),
        through=through,
        error=error,
    )


def merge_world(stored: pd.DataFrame, fresh: pd.DataFrame, *, keep_days: int) -> pd.DataFrame:
    """Add tonight's articles and drop those older than `keep_days`."""
    merged = normalize_world(pd.concat([stored, fresh], ignore_index=True))
    if merged.empty:
        return merged
    cutoff = merged["seen_at"].max() - timedelta(days=keep_days)
    kept: pd.DataFrame = merged[merged["seen_at"] >= cutoff].reset_index(drop=True)
    return kept
