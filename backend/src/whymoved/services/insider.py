from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from whymoved.domain.insider import empty_trades, normalize_trades, quarter_key
from whymoved.services.ports import InsiderTradeSource

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImportReport:
    imported: tuple[str, ...]  # quarters downloaded and merged
    unavailable: tuple[str, ...]  # quarters not (yet) published
    n_total: int  # rows stored after the import


def merge_trades(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Combine stored and fresh trades, replacing rows by filing (accession number).

    Fresh bulk rows replace any stored rows for the same filing. Rows from other sources
    (EDGAR XML) replace stored rows for the same filing unless that filing is already held
    from the bulk data, which is the more complete source and is never overridden.
    """
    if fresh.empty:
        return existing

    fresh_bulk = fresh[fresh["source"] == "bulk"]
    held_by_bulk = set(existing.loc[existing["source"] == "bulk", "accession"])
    fresh_other = fresh[(fresh["source"] != "bulk") & ~fresh["accession"].isin(held_by_bulk)]

    replaced = set(fresh_bulk["accession"]) | set(fresh_other["accession"])
    kept = existing[~existing["accession"].isin(replaced)]

    parts = [part for part in (kept, fresh_bulk, fresh_other) if not part.empty]
    if not parts:
        return empty_trades()
    merged = pd.concat(parts, ignore_index=True)
    merged = merged.sort_values(["filing_date", "accession", "trade_date"], kind="mergesort")
    return normalize_trades(merged.reset_index(drop=True))


def import_quarters(
    source: InsiderTradeSource,
    existing: pd.DataFrame,
    quarters: Sequence[tuple[int, int]],
) -> tuple[pd.DataFrame, ImportReport]:
    """Download and merge each quarter. Quarters that are not published yet are reported."""
    merged = existing
    imported: list[str] = []
    unavailable: list[str] = []
    for year, quarter in quarters:
        key = quarter_key(year, quarter)
        fresh = source.trades(year, quarter)
        if fresh is None:
            logger.warning("insider data set %s is not published yet", key)
            unavailable.append(key)
            continue
        merged = merge_trades(merged, fresh)
        imported.append(key)
        logger.info("imported %s: %d rows from this quarter", key, len(fresh))
    return merged, ImportReport(
        imported=tuple(imported), unavailable=tuple(unavailable), n_total=len(merged)
    )
