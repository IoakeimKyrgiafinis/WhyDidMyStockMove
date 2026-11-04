"""Collect the economic calendar: each release's date and first-published figure."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from whymoved.adapters.json_http import SourceError
from whymoved.domain.macro import (
    FOMC,
    MACRO_SERIES,
    empty_macro,
    fomc_meetings,
    fomc_rows,
    initial_rows,
    normalize_macro,
    release_changes,
)
from whymoved.services.ports import MacroProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MacroReport:
    n_series: int
    failed: tuple[str, ...]  # keys of the series that could not be read
    n_rows: int


def collect_macro(
    provider: MacroProvider, start: date, today: date
) -> tuple[pd.DataFrame, MacroReport]:
    """Every release from `start` to `today`. A series that fails is logged and skipped, so one
    bad answer does not lose the rest; the caller decides what to do when all of them fail."""
    rows: list[dict[str, object]] = []
    failed: list[str] = []
    for series in MACRO_SERIES:
        try:
            if series.key == FOMC:
                upper = provider.observations(series.series_id, start)
                meetings = [d for d in fomc_meetings(upper) if d.date() >= start]
                rows += fomc_rows(meetings, upper, until=pd.Timestamp(today))
            elif series.units == "lin":
                observed = provider.initial_releases(series.series_id, start)
                rows += initial_rows(series, observed)
            else:
                vintages = provider.vintages(series.series_id, start)
                rows += initial_rows(series, release_changes(series, vintages, start))
        except SourceError as exc:
            failed.append(series.key)
            logger.warning("%s: %s", series.series_id, exc)
    frame = normalize_macro(pd.DataFrame(rows)) if rows else empty_macro()
    return frame, MacroReport(n_series=len(MACRO_SERIES), failed=tuple(failed), n_rows=len(frame))


def merge_macro(stored: pd.DataFrame, fresh: pd.DataFrame, failed_keys: set[str]) -> pd.DataFrame:
    """Fresh rows replace stored ones; a series that failed tonight keeps last night's rows."""
    kept = stored[stored["key"].isin(list(failed_keys))]
    return normalize_macro(pd.concat([kept, fresh], ignore_index=True))
