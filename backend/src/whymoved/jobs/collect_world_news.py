"""Collect world press coverage of universe companies from GDELT's 15-minute files.

Each night reads the files since the last run (about 96 a day, 4 to 10 MB each). The first run
starts `--backfill-days` back; GDELT files go back years, so a longer history can be filled in
with a larger value. Articles older than `--keep-days` are dropped to keep the file small.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from whymoved.adapters.gdelt import gdelt_files, stamps
from whymoved.adapters.json_http import SourceError
from whymoved.adapters.parquet_store import StoreError
from whymoved.adapters.snapshot import SnapshotError, read_universe
from whymoved.adapters.world_store import ParquetWorldStore
from whymoved.domain.world_news import Matcher
from whymoved.services.world_news import collect_world_news, merge_world

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_BACKFILL_DAYS = 7.0
DEFAULT_KEEP_DAYS = 400
DEFAULT_MAX_MINUTES = 45.0
PUBLISH_LAG = timedelta(minutes=30)  # a file appears a few minutes after its time


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect world press coverage from GDELT.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--backfill-days", type=float, default=DEFAULT_BACKFILL_DAYS)
    parser.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    parser.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, now: datetime | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    data_dir = Path(args.data_dir)
    try:
        members, _, _ = read_universe(data_dir)
    except SnapshotError as exc:
        logger.error("no universe (run the nightly prices job first): %s", exc)
        return 2

    store = ParquetWorldStore(data_dir)
    end = (now or datetime.now(UTC)).replace(tzinfo=None) - PUBLISH_LAG
    try:
        stored = store.load()
        through = store.read_through()
    except StoreError as exc:
        logger.error("%s", exc)
        return 1
    start = through or end - timedelta(days=args.backfill_days)
    pending = stamps(start, end)
    logger.info("%d GDELT files to read from %s", len(pending), start.isoformat())

    deadline = time.monotonic() + args.max_minutes * 60.0
    try:
        with gdelt_files() as files:
            fresh, report = collect_world_news(
                files, Matcher.for_members(members), pending, deadline=deadline
            )
        store.save(merge_world(stored, fresh, keep_days=args.keep_days))
        if report.through is not None:
            store.write_through(report.through)
    except (SourceError, StoreError) as exc:
        logger.error("world news job failed: %s", exc)
        return 1
    logger.info("done: %s", report)
    return 1 if report.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
