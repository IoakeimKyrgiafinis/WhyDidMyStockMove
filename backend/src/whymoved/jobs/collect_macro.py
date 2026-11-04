"""Collect the economic calendar from FRED: Fed decisions, inflation, jobs, GDP, retail sales.

About a dozen requests: the whole window is fetched again every night, so revisions to release
dates are picked up and nothing needs merging except series that fail on a given night.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from whymoved.adapters.fred import fred_client
from whymoved.adapters.json_http import SourceError
from whymoved.adapters.macro_store import ParquetMacroStore
from whymoved.adapters.parquet_store import StoreError
from whymoved.config import MacroSettings
from whymoved.services.macro import collect_macro, merge_macro

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_DAYS = 800  # a little more than the site's two years of filings and headlines


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect the economic calendar from FRED.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # httpx logs every request URL at INFO, and FRED takes its key in the URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = MacroSettings()
    except ValidationError:
        logger.error("configuration error: WHYMOVED_FRED_API_KEY is not set")
        return 2

    today = datetime.now(UTC).date()
    start = today - timedelta(days=args.days)
    store = ParquetMacroStore(Path(args.data_dir))
    try:
        stored = store.load()
        with fred_client(settings.fred_api_key.get_secret_value()) as fred:
            fresh, report = collect_macro(fred, start, today)
        if len(report.failed) == report.n_series:
            logger.error("every series failed; keeping the stored calendar")
            return 1
        store.save(merge_macro(stored, fresh, set(report.failed)))
    except (SourceError, StoreError) as exc:
        logger.error("economic calendar job failed: %s", exc)
        return 1
    logger.info("done: %d releases; failed: %s", report.n_rows, ", ".join(report.failed) or "none")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
