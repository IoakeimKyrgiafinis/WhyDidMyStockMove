from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from whymoved.domain.bars import DailyBars
from whymoved.domain.quality import (
    DEFAULT_DUPLICATE_THRESHOLD,
    QualityConfig,
    assess_quality,
    drop_near_duplicates,
    trading_calendar,
)

DEFAULT_LIQUIDITY_DAYS = 60


class DataQualityError(Exception):
    """A series the whole product depends on (market or sector factor) failed its checks."""


@dataclass(frozen=True, eq=False)
class Selection:
    """The stocks that are clean enough to use, and why the others were left out."""

    tickers: list[str]  # clean stocks, most liquid first
    quality: pd.DataFrame  # the quality report for every column
    failed: dict[str, str]  # stock -> reasons it failed the quality checks
    duplicates: dict[str, str]  # dropped stock -> the stock it duplicates
    calendar: pd.DatetimeIndex


def select_universe(
    bars: DailyBars,
    stocks: Sequence[str],
    factors: Sequence[str],
    config: QualityConfig | None = None,
    duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
    liquidity_days: int = DEFAULT_LIQUIDITY_DAYS,
) -> Selection:
    """Apply the quality checks and remove near-duplicate stocks.

    Factor series (market and sector ETFs) must pass: a failure raises DataQualityError.
    Stocks that fail are reported and left out. Among near-duplicates (share classes), the
    one with the higher median dollar volume over the last `liquidity_days` is kept.
    """
    cfg = config or QualityConfig()
    if liquidity_days < 1:
        raise ValueError("liquidity_days must be at least 1")

    names = list(stocks)
    factor_names = list(factors)
    missing = [t for t in [*names, *factor_names] if t not in bars.close.columns]
    if missing:
        raise ValueError(f"bars are missing columns: {missing[:5]}")

    calendar = trading_calendar(bars.close)
    report = assess_quality(bars.close, calendar, cfg)

    bad_factors = [f for f in factor_names if not bool(report.loc[f, "passed"])]
    if bad_factors:
        detail = "; ".join(f"{f} ({str(report.loc[f, 'reasons'])})" for f in bad_factors)
        raise DataQualityError(f"factor series failed the quality checks: {detail}")

    failed = {t: str(report.loc[t, "reasons"]) for t in names if not bool(report.loc[t, "passed"])}
    passing = [t for t in names if t not in failed]
    if not passing:
        return Selection(
            tickers=[], quality=report, failed=failed, duplicates={}, calendar=calendar
        )

    closes = bars.close.reindex(calendar)[passing]
    volumes = bars.volume.reindex(calendar)[passing]

    # Short gaps are carried forward so the duplicate check sees continuous returns.
    filled = closes.ffill(limit=cfg.max_gap_days)
    returns = (filled / filled.shift(1) - 1.0).iloc[1:].fillna(0.0)

    liquidity = (closes * volumes).tail(liquidity_days).median(axis=0, skipna=True).fillna(0.0)
    priority = sorted(passing, key=lambda t: (-float(liquidity[t]), t))
    kept, dropped = drop_near_duplicates(returns, priority, duplicate_threshold)

    return Selection(
        tickers=kept, quality=report, failed=failed, duplicates=dropped, calendar=calendar
    )
