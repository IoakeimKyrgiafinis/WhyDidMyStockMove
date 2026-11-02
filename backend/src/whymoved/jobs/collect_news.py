from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from whymoved.adapters.alpaca import AlpacaError, build_client
from whymoved.adapters.alpaca_news import AlpacaNews
from whymoved.adapters.news_store import ParquetNewsStore
from whymoved.adapters.parquet_store import StoreError
from whymoved.config import JobSettings
from whymoved.services.news import collect_news

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_YEARS = 2.0


def load_universe_symbols(path: Path) -> list[str]:
    """The tickers of the clean universe, as written by the nightly price job."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read the universe file {path}: {exc}") from exc
    members = data.get("members") if isinstance(data, dict) else None
    if not isinstance(members, list):
        raise ValueError("the universe file has no members list")
    tickers = [
        entry["ticker"]
        for entry in members
        if isinstance(entry, dict) and isinstance(entry.get("ticker"), str)
    ]
    unique = list(dict.fromkeys(tickers))
    if not unique:
        raise ValueError("the universe file has no members")
    return unique


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect headlines for the clean universe.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--end", type=datetime.fromisoformat, default=None)
    parser.add_argument("--force-full", action="store_true", help="refetch all history")
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

    end: datetime = args.end or datetime.now(UTC).replace(tzinfo=None)
    history_start = end - timedelta(days=int(args.years * 365.25))
    client = build_client(settings.alpaca_key_id, settings.alpaca_secret_key)

    try:
        with AlpacaNews(client) as provider:
            _, report = collect_news(
                provider,
                ParquetNewsStore(data_dir),
                symbols,
                history_start=history_start,
                end=end,
                force_full=args.force_full,
            )
    except (AlpacaError, StoreError, ValueError) as exc:
        logger.error("news job failed: %s", exc)
        return 1

    logger.info("done: %d headlines stored (%d new)", report.n_total, report.n_new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
