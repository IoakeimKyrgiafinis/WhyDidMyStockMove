from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from whymoved.adapters.alpaca import AlpacaError, from_settings
from whymoved.adapters.parquet_store import ParquetPriceStore, StoreError
from whymoved.config import JobSettings
from whymoved.domain.quality import QualityConfig
from whymoved.domain.selection import DataQualityError, Selection, select_universe
from whymoved.domain.universe import (
    MARKET_ETF,
    SECTOR_ETF_TICKERS,
    UniverseMember,
    build_universe,
    parse_holdings,
)
from whymoved.services.ports import PriceProvider, PriceStore
from whymoved.services.prices import DEFAULT_OVERLAP_DAYS, UpdateReport, update_prices

logger = logging.getLogger(__name__)

DEFAULT_HOLDINGS = Path("universe/iwv_holdings.csv")
DEFAULT_DATA_DIR = Path("data")
DEFAULT_YEARS = 7.0


@dataclass(frozen=True, eq=False)
class NightlyResult:
    as_of: date
    history_start: date
    n_holdings: int
    members: list[UniverseMember]  # clean members, most liquid first
    unmapped: list[str]  # holdings dropped for an untracked sector
    update: UpdateReport
    selection: Selection


def run_nightly(
    provider: PriceProvider,
    store: PriceStore,
    holdings_text: str,
    *,
    history_start: date,
    end: date,
    config: QualityConfig | None = None,
    limit: int | None = None,
    force_full: bool = False,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
) -> NightlyResult:
    """Update prices for the universe and decide which stocks are clean enough to use."""
    holdings = parse_holdings(holdings_text)
    members, unmapped = build_universe(holdings)
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        members = members[:limit]
    if not members:
        raise ValueError("the universe is empty")

    stocks = [m.ticker for m in members]
    factors = [MARKET_ETF, *SECTOR_ETF_TICKERS]
    symbols = [*factors, *[s for s in stocks if s not in set(factors)]]

    bars, update = update_prices(
        provider,
        store,
        symbols,
        history_start=history_start,
        end=end,
        overlap_days=overlap_days,
        force_full=force_full,
    )
    selection = select_universe(bars, stocks, factors, config)

    by_ticker = {m.ticker: m for m in members}
    clean = [by_ticker[t] for t in selection.tickers]
    logger.info(
        "universe: %d holdings, %d clean, %d failed quality, %d duplicates",
        len(holdings),
        len(clean),
        len(selection.failed),
        len(selection.duplicates),
    )
    return NightlyResult(
        as_of=end,
        history_start=history_start,
        n_holdings=len(holdings),
        members=clean,
        unmapped=unmapped,
        update=update,
        selection=selection,
    )


def _atomic_write_text(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_outputs(directory: Path, result: NightlyResult) -> None:
    """Write the quality report (parquet) and the universe summary (JSON)."""
    directory.mkdir(parents=True, exist_ok=True)

    temp = directory / ".quality.parquet.tmp"
    try:
        result.selection.quality.to_parquet(temp, engine="pyarrow")
        os.replace(temp, directory / "quality.parquet")
    finally:
        temp.unlink(missing_ok=True)

    selection = result.selection
    summary = {
        "as_of": result.as_of.isoformat(),
        "history_start": result.history_start.isoformat(),
        "counts": {
            "holdings": result.n_holdings,
            "clean": len(result.members),
            "failed_quality": len(selection.failed),
            "duplicates": len(selection.duplicates),
            "unmapped_sector": len(result.unmapped),
        },
        "members": [
            {
                "ticker": m.ticker,
                "name": m.name,
                "sector": m.sector,
                "sector_etf": m.sector_etf,
                "weight": m.weight,
            }
            for m in result.members
        ],
        "failed_quality": selection.failed,
        "duplicates": selection.duplicates,
        "unmapped_sector": result.unmapped,
        "update": {
            "new_symbols": list(result.update.new_symbols),
            "refetched": list(result.update.refetched),
        },
    }
    _atomic_write_text(directory / "universe.json", json.dumps(summary, indent=2))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update prices and rebuild the clean universe.")
    parser.add_argument("--holdings", type=Path, default=DEFAULT_HOLDINGS)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--limit", type=int, default=None, help="use only the top N holdings")
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

    try:
        holdings_text = Path(args.holdings).read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("cannot read the holdings file %s: %s", args.holdings, exc)
        return 2

    end: date = args.end or (datetime.now(UTC).date() - timedelta(days=1))
    history_start = end - timedelta(days=int(args.years * 365.25))
    data_dir = Path(args.data_dir)

    try:
        with from_settings(settings) as provider:
            result = run_nightly(
                provider,
                ParquetPriceStore(data_dir),
                holdings_text,
                history_start=history_start,
                end=end,
                limit=args.limit,
                force_full=args.force_full,
            )
        save_outputs(data_dir, result)
    except (AlpacaError, DataQualityError, StoreError, ValueError) as exc:
        logger.error("nightly job failed: %s", exc)
        return 1

    logger.info("done: %d clean stocks as of %s", len(result.members), result.as_of)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
