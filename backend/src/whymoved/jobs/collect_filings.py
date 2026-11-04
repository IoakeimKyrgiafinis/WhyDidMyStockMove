from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from whymoved.adapters.edgar import from_settings
from whymoved.adapters.edgar_errors import EdgarError
from whymoved.adapters.filings_store import ParquetEventStore, load_seen, save_seen
from whymoved.adapters.insider_store import ParquetTradeStore
from whymoved.adapters.parquet_store import StoreError
from whymoved.config import JobSettings
from whymoved.jobs.collect_insider import build_ticker_by_cik
from whymoved.jobs.collect_news import load_universe_symbols
from whymoved.services.filings import collect_filings, gap_start, merge_events
from whymoved.services.insider import merge_trades

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_EVENTS_YEARS = 2.0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill the insider gap and collect company filings."
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--events-years", type=float, default=DEFAULT_EVENTS_YEARS)
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
    events_since = today - timedelta(days=int(args.events_years * 365.25))

    try:
        with from_settings(settings) as edgar:
            ticker_by_cik, missing = build_ticker_by_cik(symbols, edgar.ticker_map())
            if missing:
                logger.warning("no SEC CIK for %d universe tickers", len(missing))
            ciks = {symbol: cik for cik, symbol in ticker_by_cik.items()}

            trade_store = ParquetTradeStore(data_dir)
            event_store = ParquetEventStore(data_dir)
            trades = trade_store.load()
            events = event_store.load()

            form4_since = gap_start(trades, today)
            seen = {a: d for a, d in load_seen(data_dir).items() if d >= form4_since}
            known = set(trades["accession"]) | set(seen)
            logger.info("gap fill from %s, company filings from %s", form4_since, events_since)

            result = collect_filings(
                edgar, ciks, known, form4_since=form4_since, events_since=events_since
            )
            trade_store.save(merge_trades(trades, result.trades))
            event_store.save(merge_events(events, result.events, cutoff=events_since))
            save_seen(data_dir, {**seen, **result.seen})
    except (EdgarError, StoreError, ValueError) as exc:
        logger.error("filings job failed: %s", exc)
        return 1

    logger.info("done: %s", result.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
