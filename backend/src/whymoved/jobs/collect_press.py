"""Read the press releases attached to stored 8-Ks and 6-Ks.

Each filing costs two SEC requests, so a first run would take hours for two years of filings.
Runs are capped by count and time and work newest first; the history fills in over a few
nights, and afterwards each night only checks the day's new filings.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from whymoved.adapters.edgar import from_settings
from whymoved.adapters.edgar_errors import EdgarError
from whymoved.adapters.filings_store import ParquetEventStore
from whymoved.adapters.parquet_store import StoreError
from whymoved.adapters.press_store import ParquetPressStore
from whymoved.config import JobSettings
from whymoved.services.press import collect_press_releases, merge_press, pending_filings

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_MAX_FILINGS = 10_000  # about 45 minutes at the SEC's rate limit
DEFAULT_MAX_MINUTES = 60.0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read press releases attached to filings.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--max-filings", type=int, default=DEFAULT_MAX_FILINGS)
    parser.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        settings = JobSettings()
    except ValidationError as exc:
        logger.error("configuration error: %s", exc)
        return 2

    data_dir = Path(args.data_dir)
    store = ParquetPressStore(data_dir)
    try:
        events = ParquetEventStore(data_dir).load()
        stored = store.load()
    except StoreError as exc:
        logger.error("%s", exc)
        return 1
    if events.empty:
        logger.error("no stored filings (run the filings job first)")
        return 2

    keep = set(events["accession"])
    progress = {"rows": stored}

    def checkpoint(batch: pd.DataFrame) -> None:
        progress["rows"] = merge_press(progress["rows"], batch, keep)
        store.save(progress["rows"])

    pending = pending_filings(events, set(stored["accession"]))
    deadline = time.monotonic() + args.max_minutes * 60.0
    try:
        with from_settings(settings) as edgar:
            fresh, report = collect_press_releases(
                edgar, pending, max_filings=args.max_filings, deadline=deadline, on_batch=checkpoint
            )
    except (EdgarError, StoreError) as exc:
        logger.error("press-release job failed: %s", exc)
        return 1
    store.save(merge_press(progress["rows"], fresh, keep))
    logger.info("done: %s; %d still to check", report, report.n_pending - report.n_checked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
