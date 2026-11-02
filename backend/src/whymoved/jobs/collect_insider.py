from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from whymoved.adapters.edgar import from_settings
from whymoved.adapters.edgar_errors import EdgarError
from whymoved.adapters.insider_bulk import BulkFormatError
from whymoved.adapters.insider_source import EdgarInsiderSource
from whymoved.adapters.insider_store import ParquetTradeStore, load_imported, save_imported
from whymoved.adapters.parquet_store import StoreError
from whymoved.config import JobSettings
from whymoved.domain.insider import plan_quarters
from whymoved.jobs.collect_news import load_universe_symbols
from whymoved.services.insider import import_quarters

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_YEARS = 7.0


def build_ticker_by_cik(
    symbols: Sequence[str], tickers: Mapping[str, int]
) -> tuple[dict[int, str], list[str]]:
    """CIK to universe ticker, and the universe tickers the SEC has no CIK for.

    If several tickers share a CIK (share classes), the one listed first wins.
    """
    mapping: dict[int, str] = {}
    missing: list[str] = []
    for symbol in reversed(symbols):
        cik = tickers.get(symbol)
        if cik is None:
            missing.append(symbol)
        else:
            mapping[cik] = symbol
    missing.reverse()
    return mapping, missing


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import the SEC's bulk insider trade data.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--today", type=date.fromisoformat, default=None)
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
    try:
        symbols = load_universe_symbols(data_dir / "universe.json")
    except ValueError as exc:
        logger.error("%s (run the nightly price job first)", exc)
        return 2

    today: date = args.today or datetime.now(UTC).date()
    history_start = today - timedelta(days=int(args.years * 365.25))

    try:
        with from_settings(settings) as edgar:
            ticker_by_cik, missing = build_ticker_by_cik(symbols, edgar.ticker_map())
            if missing:
                logger.warning(
                    "no SEC CIK for %d universe tickers, e.g. %s", len(missing), missing[:5]
                )

            store = ParquetTradeStore(data_dir)
            imported = load_imported(data_dir)
            quarters = plan_quarters(history_start, today, imported)
            logger.info("importing %d quarters", len(quarters))

            done = set(imported)

            def checkpoint(merged: pd.DataFrame, key: str) -> None:
                store.save(merged)
                done.add(key)
                save_imported(data_dir, done)

            merged, report = import_quarters(
                EdgarInsiderSource(edgar, ticker_by_cik), store.load(), quarters, checkpoint
            )
    except (EdgarError, BulkFormatError, StoreError, ValueError) as exc:
        logger.error("insider job failed: %s", exc)
        return 1

    logger.info(
        "done: %d trades stored, %d quarters imported, %d unavailable",
        report.n_total,
        len(report.imported),
        len(report.unavailable),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
