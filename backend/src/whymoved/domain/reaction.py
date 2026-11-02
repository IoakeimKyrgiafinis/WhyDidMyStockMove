"""Do a stock's announcement days move more than its ordinary days?

Direction is hard to call (good and bad news cancel out in an average), but size is not:
earnings days are usually much bigger than ordinary days. This compares the average absolute
score on event days with the same number of random scored days.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from whymoved.domain.events import MAX_SNAP_DAYS

DEFAULT_N_PERM = 5000
MIN_EVENTS = 2


@dataclass(frozen=True)
class ReactionSize:
    n_events: int
    mean_abs_z_events: float
    mean_abs_z_all: float
    ratio: float
    p_value: float  # share of random draws with a mean at least as large (one-sided)
    n_permutations: int


def event_positions(
    index: pd.DatetimeIndex, events: Sequence[pd.Timestamp], max_snap_days: int = MAX_SNAP_DAYS
) -> np.ndarray:
    """Positions of the first trading day on or after each event (unique, sorted)."""
    if len(index) == 0 or not events:
        return np.array([], dtype=np.int64)
    days = index.to_numpy().astype("datetime64[D]").astype(np.int64)
    targets = np.array(
        [pd.Timestamp(e).to_datetime64().astype("datetime64[D]").astype(np.int64) for e in events],
        dtype=np.int64,
    )
    pos = np.searchsorted(days, targets, side="left")
    ok = pos < len(days)
    ok[ok] &= (days[pos[ok]] - targets[ok]) <= max_snap_days
    return np.unique(pos[ok]).astype(np.int64)


def reaction_size(
    z: pd.Series[float],
    events: Sequence[pd.Timestamp],
    n_perm: int = DEFAULT_N_PERM,
    seed: int = 0,
) -> ReactionSize | None:
    """Compare |z| on event days with random days. None with fewer than MIN_EVENTS events."""
    if not isinstance(z.index, pd.DatetimeIndex):
        raise ValueError("z needs a DatetimeIndex")
    values = np.abs(np.asarray(z, dtype=np.float64))
    pos = event_positions(z.index, events)
    pos = pos[np.isfinite(values[pos])]
    eligible = np.nonzero(np.isfinite(values))[0]
    k = len(pos)
    if k < MIN_EVENTS or len(eligible) <= k:
        return None

    observed = float(values[pos].mean())
    rng = np.random.default_rng(seed)
    null = np.array(
        [values[rng.choice(eligible, size=k, replace=False)].mean() for _ in range(n_perm)]
    )
    overall = float(values[eligible].mean())
    return ReactionSize(
        n_events=k,
        mean_abs_z_events=observed,
        mean_abs_z_all=overall,
        ratio=observed / overall if overall > 0 else float("nan"),
        p_value=(1.0 + float(np.sum(null >= observed))) / (n_perm + 1.0),
        n_permutations=n_perm,
    )
