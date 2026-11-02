from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from whymoved.domain.peers import correlation_matrix

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

DEFAULT_DUPLICATE_THRESHOLD = 0.98


@dataclass(frozen=True)
class QualityConfig:
    """Limits a stock must meet to enter the universe. Starting points, to be tuned on data."""

    min_coverage: float = 0.95
    max_gap_days: int = 5
    min_median_price: float = 3.0
    max_abs_return: float = 0.6
    max_extreme_moves: int = 2
    max_zero_return_share: float = 0.25

    def __post_init__(self) -> None:
        if not 0.0 < self.min_coverage <= 1.0:
            raise ValueError("min_coverage must be in (0, 1]")
        if self.max_gap_days < 0:
            raise ValueError("max_gap_days must be non-negative")
        if self.min_median_price < 0.0:
            raise ValueError("min_median_price must be non-negative")
        if self.max_abs_return <= 0.0:
            raise ValueError("max_abs_return must be positive")
        if self.max_extreme_moves < 0:
            raise ValueError("max_extreme_moves must be non-negative")
        if not 0.0 <= self.max_zero_return_share <= 1.0:
            raise ValueError("max_zero_return_share must be in [0, 1]")


def _longest_run(mask: BoolArray) -> int:
    """Length of the longest run of True values."""
    if not bool(mask.any()):
        return 0
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return int((edges[1::2] - edges[::2]).max())


def _assess_column(col: FloatArray, cfg: QualityConfig) -> dict[str, Any]:
    valid = np.isfinite(col)
    prices = col[valid]
    n_obs = int(valid.sum())
    coverage = n_obs / len(col)
    gap = _longest_run(~valid)
    nonpositive = bool((prices <= 0.0).any())
    median = float(np.median(prices)) if n_obs else float("nan")

    extreme = 0
    zero_share = float("nan")
    if n_obs >= 2 and not nonpositive:
        step = prices[1:] / prices[:-1] - 1.0
        extreme = int(np.sum(np.abs(step) > cfg.max_abs_return))
        zero_share = float(np.mean(step == 0.0))

    reasons: list[str] = []
    if coverage < cfg.min_coverage:
        reasons.append(f"coverage {coverage:.0%} is below {cfg.min_coverage:.0%}")
    if gap > cfg.max_gap_days:
        reasons.append(f"{gap} trading days missing in a row (limit {cfg.max_gap_days})")
    if nonpositive:
        reasons.append("non-positive prices")
    if n_obs and median < cfg.min_median_price:
        reasons.append(f"median price ${median:.2f} is below ${cfg.min_median_price:.2f}")
    if extreme > cfg.max_extreme_moves:
        reasons.append(
            f"{extreme} daily moves above {cfg.max_abs_return:.0%}; possible split or bad data"
        )
    if not np.isnan(zero_share) and zero_share > cfg.max_zero_return_share:
        reasons.append(f"{zero_share:.0%} of days have an unchanged price; likely stale data")

    return {
        "coverage": coverage,
        "n_obs": n_obs,
        "longest_gap": gap,
        "median_price": median,
        "extreme_moves": extreme,
        "zero_return_share": zero_share,
        "passed": not reasons,
        "reasons": "; ".join(reasons),
    }


def assess_quality(
    closes: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    config: QualityConfig | None = None,
) -> pd.DataFrame:
    """Check every stock's daily closes against the trading calendar.

    `closes` is wide (one column per ticker, NaN for missing days). Days outside the
    calendar are ignored. Columns of the result: coverage, n_obs, longest_gap,
    median_price, extreme_moves, zero_return_share, passed, reasons.

    Coverage is measured over the whole calendar, so recent listings with a short
    history fail it. Unchanged prices catch the stale closes that thinly traded stocks
    produce on a partial feed.
    """
    cfg = config or QualityConfig()
    if len(calendar) == 0:
        raise ValueError("calendar is empty")
    if not calendar.is_monotonic_increasing or not calendar.is_unique:
        raise ValueError("calendar must be sorted and unique")
    if not isinstance(closes.index, pd.DatetimeIndex):
        raise ValueError("closes needs a DatetimeIndex")
    if not closes.index.is_unique:
        raise ValueError("closes has duplicate dates")
    if not closes.columns.is_unique:
        raise ValueError("column names must be unique")
    if closes.shape[1] == 0:
        raise ValueError("no columns")

    values = closes.reindex(calendar).to_numpy(dtype=np.float64)
    rows = [_assess_column(values[:, j], cfg) for j in range(values.shape[1])]
    return pd.DataFrame(rows, index=pd.Index([str(c) for c in closes.columns], name="ticker"))


def drop_near_duplicates(
    returns: pd.DataFrame,
    priority: Sequence[str],
    threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
) -> tuple[list[str], dict[str, str]]:
    """Drop stocks whose returns are almost identical to a higher-priority stock.

    Handles share classes of one company (GOOG and GOOGL) and other clones, which would
    otherwise be every other's top peer. `priority` lists tickers from most to least
    preferred, for example by trading volume. `returns` must be clean (no NaN). Returns
    (kept tickers in priority order, dropped ticker mapped to the stock it duplicates).
    """
    names = list(priority)
    if len(set(names)) != len(names):
        raise ValueError("priority must not contain duplicates")
    unknown = [t for t in names if t not in returns.columns]
    if unknown:
        raise ValueError(f"priority contains unknown tickers: {unknown[:5]}")
    if not 0.0 < threshold <= 1.0:
        raise ValueError("threshold must be in (0, 1]")

    corr = np.nan_to_num(np.abs(correlation_matrix(returns[names]).to_numpy()), nan=0.0)
    kept: list[int] = []
    dropped: dict[str, str] = {}
    for i, ticker in enumerate(names):
        if kept:
            row = corr[i, kept]
            best = int(np.argmax(row))
            if float(row[best]) >= threshold:
                dropped[ticker] = names[kept[best]]
                continue
        kept.append(i)
    return [names[i] for i in kept], dropped


DEFAULT_CALENDAR_SHARE = 0.5


def trading_calendar(
    closes: pd.DataFrame, min_share: float = DEFAULT_CALENDAR_SHARE
) -> pd.DatetimeIndex:
    """Trading days inferred from the data.

    A date counts when at least `min_share` of the symbols that have any data at all have a
    close on it. Symbols with no data are ignored, so dead tickers cannot distort the calendar.
    """
    if not 0.0 < min_share <= 1.0:
        raise ValueError("min_share must be in (0, 1]")
    if not isinstance(closes.index, pd.DatetimeIndex):
        raise ValueError("closes needs a DatetimeIndex")
    if not closes.index.is_unique:
        raise ValueError("closes has duplicate dates")

    present = closes.notna()
    active = present.any(axis=0).to_numpy()
    n_active = int(active.sum())
    if n_active == 0:
        raise ValueError("no symbol has any data")

    share = present.loc[:, active].sum(axis=1).to_numpy(dtype=np.float64) / n_active
    return pd.DatetimeIndex(closes.index[share >= min_share]).sort_values()
