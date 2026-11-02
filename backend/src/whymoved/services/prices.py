from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from whymoved.domain.bars import DailyBars
from whymoved.services.ports import PriceProvider, PriceStore

logger = logging.getLogger(__name__)

DEFAULT_OVERLAP_DAYS = 10
DEFAULT_TOLERANCE = 1e-6


@dataclass(frozen=True)
class UpdateReport:
    n_symbols: int
    new_symbols: tuple[str, ...]  # no stored data: fetched in full
    refetched: tuple[str, ...]  # adjusted history changed: fetched in full again
    first_date: date | None
    last_date: date | None
    forced: bool


def changed_symbols(
    stored: pd.DataFrame, fresh: pd.DataFrame, rel_tol: float = DEFAULT_TOLERANCE
) -> list[str]:
    """Symbols whose stored prices disagree with freshly fetched prices on shared dates.

    Splits and dividends rewrite a stock's entire adjusted history, so a mismatch on the
    recent overlap means the stored history is out of date. Missing values are ignored.
    """
    if rel_tol < 0.0:
        raise ValueError("rel_tol must be non-negative")
    columns = [str(c) for c in fresh.columns if c in stored.columns]
    dates = fresh.index.intersection(stored.index)
    if not columns or len(dates) == 0:
        return []

    old = stored.loc[dates, columns].to_numpy(dtype=np.float64)
    new = fresh.loc[dates, columns].to_numpy(dtype=np.float64)
    both = np.isfinite(old) & np.isfinite(new)
    with np.errstate(invalid="ignore", divide="ignore"):
        relative = np.abs(new - old) / np.abs(old)
    differs = both & (relative > rel_tol)
    return [columns[j] for j in np.flatnonzero(differs.any(axis=0))]


def _merge_frame(
    existing: pd.DataFrame | None,
    incremental: pd.DataFrame | None,
    full: pd.DataFrame | None,
    symbols: Sequence[str],
) -> pd.DataFrame:
    merged = existing
    if merged is not None and full is not None:
        merged = merged.drop(columns=[c for c in full.columns if c in merged.columns])
    if incremental is not None:
        merged = incremental if merged is None else incremental.combine_first(merged)
    if full is not None:
        merged = full if merged is None else full.combine_first(merged)
    if merged is None:
        raise ValueError("nothing to merge")
    return merged.reindex(columns=list(symbols)).sort_index()


def merge_bars(
    existing: DailyBars | None,
    incremental: DailyBars | None,
    full: DailyBars | None,
    symbols: Sequence[str],
) -> DailyBars:
    """Combine stored history with newly fetched data.

    `incremental` updates symbols that keep their history: fresh values win, stored values
    fill the gaps. `full` replaces its symbols' columns completely. The result has exactly
    the requested symbols, in that order, and no rows without any close.
    """
    close = _merge_frame(
        existing.close if existing is not None else None,
        incremental.close if incremental is not None else None,
        full.close if full is not None else None,
        symbols,
    ).astype("float64")
    volume = _merge_frame(
        existing.volume if existing is not None else None,
        incremental.volume if incremental is not None else None,
        full.volume if full is not None else None,
        symbols,
    ).astype("float64")

    close = close.loc[close.notna().any(axis=1)].rename_axis("date")
    volume = volume.reindex(index=close.index, columns=close.columns).rename_axis("date")
    return DailyBars(close=close, volume=volume)


def update_prices(
    provider: PriceProvider,
    store: PriceStore,
    symbols: Sequence[str],
    *,
    history_start: date,
    end: date,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
    tolerance: float = DEFAULT_TOLERANCE,
    force_full: bool = False,
) -> tuple[DailyBars, UpdateReport]:
    """Bring the stored price history up to `end` and save it.

    Symbols with stored data are fetched from `overlap_days` before the last stored date.
    If their overlap no longer matches the stored prices, or they have no stored data, they
    are fetched in full from `history_start`. A symbol that returns no data at all stays as
    an empty column and is requested in full again on the next run.
    """
    names = list(symbols)
    if not names:
        raise ValueError("symbols must not be empty")
    if len(set(names)) != len(names):
        raise ValueError("symbols must be unique")
    if history_start > end:
        raise ValueError("history_start must not be after end")
    if overlap_days < 1:
        raise ValueError("overlap_days must be at least 1")

    existing = store.load()
    stored: set[str] = set()
    if existing is not None and not force_full:
        has_data = existing.close.notna().any(axis=0)
        stored = {str(label) for label, ok in has_data.items() if bool(ok)}

    incremental = [s for s in names if s in stored]
    new_symbols = [s for s in names if s not in stored]

    fresh_incremental: DailyBars | None = None
    refetched: list[str] = []
    if incremental and existing is not None:
        last = pd.Timestamp(existing.close.index.max()).date()
        start = max(last - timedelta(days=overlap_days), history_start)
        if start <= end:
            fresh = provider.daily_bars(incremental, start, end)
            refetched = changed_symbols(existing.close, fresh.close, tolerance)
            keep = [s for s in incremental if s not in refetched]
            fresh_incremental = DailyBars(
                close=fresh.close.reindex(columns=keep),
                volume=fresh.volume.reindex(columns=keep),
            )

    needs_full = set(new_symbols) | set(refetched)
    full_names = [s for s in names if s in needs_full]
    fresh_full = provider.daily_bars(full_names, history_start, end) if full_names else None

    merged = merge_bars(existing, fresh_incremental, fresh_full, names)
    store.save(merged)

    dates = merged.close.index
    report = UpdateReport(
        n_symbols=len(names),
        new_symbols=tuple(new_symbols),
        refetched=tuple(refetched),
        first_date=pd.Timestamp(dates.min()).date() if len(dates) else None,
        last_date=pd.Timestamp(dates.max()).date() if len(dates) else None,
        forced=force_full,
    )
    logger.info(
        "prices updated: %d symbols, %d fetched in full, %d refetched after adjustments",
        report.n_symbols,
        len(new_symbols),
        len(refetched),
    )
    return merged, report
