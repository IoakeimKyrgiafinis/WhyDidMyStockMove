from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass

import pandas as pd

from whymoved.domain.filings import FilingFormatError, FilingNotFound
from whymoved.domain.press import empty_press, normalize_press, press_row
from whymoved.services.ports import PressReleaseProvider

logger = logging.getLogger(__name__)

# Press releases are attached as exhibits, which an 8-K lists under Item 9.01. A 6-K has no
# items, so every 6-K is checked.
EXHIBIT_ITEM = "9.01"


@dataclass(frozen=True)
class PressReport:
    n_pending: int  # filings that may carry a release and were never checked
    n_checked: int  # checked this run
    n_found: int  # with a readable release
    n_failed: int  # not reachable this run; tried again next run


def pending_filings(events: pd.DataFrame, known: Collection[str]) -> pd.DataFrame:
    """Filings that may carry a press release and have not been checked yet, newest first, so a
    capped run fills the recent past before older history."""
    has_exhibits = (events["form"] == "6-K") | (
        (events["form"] == "8-K")
        & events["items"].map(lambda items: EXHIBIT_ITEM in str(items).split(","))
    )
    todo = events[has_exhibits & ~events["accession"].isin(set(known))]
    return todo.sort_values(["filing_date", "accession"], ascending=False, kind="mergesort")


def collect_press_releases(
    provider: PressReleaseProvider,
    pending: pd.DataFrame,
    *,
    max_filings: int,
    deadline: float | None = None,
    on_batch: Callable[[pd.DataFrame], None] | None = None,
    batch_size: int = 500,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[pd.DataFrame, PressReport]:
    """Check up to `max_filings` filings (stopping early at `deadline`, a `clock` value).

    `on_batch` receives every `batch_size` new rows, so a long backfill can save its progress.
    A filing the SEC cannot find right now is skipped and tried again next run; a malformed one
    is recorded empty so it is not.
    """
    if max_filings < 0:
        raise ValueError("max_filings must be non-negative")
    rows: list[dict[str, str]] = []
    batch_start = 0
    checked = found = failed = 0
    for r in pending.head(max_filings).itertuples(index=False):
        if deadline is not None and clock() >= deadline:
            logger.info("time budget reached after %d filings", checked)
            break
        accession, ticker = str(r.accession), str(r.ticker)
        try:
            result = provider.press_release(int(str(r.cik)), accession)
        except FilingNotFound as exc:
            failed += 1
            logger.warning("skipping %s: %s", accession, exc)
            continue
        except FilingFormatError as exc:
            logger.warning("unreadable filing %s: %s", accession, exc)
            result = None
        checked += 1
        url, release = result if result is not None else (None, None)
        found += release is not None
        rows.append(press_row(accession, ticker, url, release))
        if on_batch is not None and len(rows) - batch_start >= batch_size:
            on_batch(normalize_press(pd.DataFrame(rows)))
            batch_start = len(rows)

    fresh = normalize_press(pd.DataFrame(rows)) if rows else empty_press()
    report = PressReport(n_pending=len(pending), n_checked=checked, n_found=found, n_failed=failed)
    logger.info(
        "press releases: %d pending, %d checked, %d found, %d failed",
        len(pending),
        checked,
        found,
        failed,
    )
    return fresh, report


def merge_press(existing: pd.DataFrame, fresh: pd.DataFrame, keep: Collection[str]) -> pd.DataFrame:
    """Stored and fresh rows, fresh winning; only filings in `keep` (the stored events) stay, so
    releases leave together with their filings."""
    kept = existing[~existing["accession"].isin(set(fresh["accession"]))]
    parts = [part for part in (kept, fresh) if not part.empty]
    if not parts:
        return empty_press()
    merged = pd.concat(parts, ignore_index=True)
    return normalize_press(merged[merged["accession"].isin(set(keep))])
