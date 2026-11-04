"""Collect FDA approvals and recalls (openFDA) and Phase 3 trial news (ClinicalTrials.gov) for
the universe's health care companies. No keys; about 30 openFDA requests and one
ClinicalTrials.gov request per company. The whole window is read again every night.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from whymoved.adapters.health_sources import clinical_trials, open_fda
from whymoved.adapters.health_store import ParquetHealthStore
from whymoved.adapters.parquet_store import StoreError
from whymoved.adapters.snapshot import SnapshotError, read_universe
from whymoved.services.health import collect_health, merge_health

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_DAYS = 800


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect FDA and clinical-trial events.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
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
    until = datetime.now(UTC).date()
    since = until - timedelta(days=args.days)
    store = ParquetHealthStore(data_dir)
    try:
        stored = store.load()
        fresh, report = collect_health(open_fda(), clinical_trials(), members, since, until)
        store.save(merge_health(stored, fresh, report))
    except StoreError as exc:
        logger.error("health events job failed: %s", exc)
        return 1
    logger.info("done: %s", report)
    failed = report.failed_kinds or len(report.failed_tickers) > report.n_companies // 10
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
