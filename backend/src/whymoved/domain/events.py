from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.stats import t as student_t

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]
Alternative = Literal["two-sided", "greater", "less"]

MIN_EVENTS = 2
MAX_SNAP_DAYS = 5
DEFAULT_LEVEL = 0.95
DEFAULT_N_PERM = 10_000
MIN_PERM = 100


@dataclass(frozen=True, eq=False)
class EventWindows:
    """Abnormal returns and volatility forecasts around each usable event.

    abnormal and sigma have shape (n_events, pre + post + 1); column j is relative
    day j - pre, so the event day itself is column `pre`.
    """

    relative_days: IntArray
    positions: IntArray
    dates: tuple[pd.Timestamp, ...]
    abnormal: FloatArray
    sigma: FloatArray
    n_requested: int
    n_dropped_unmapped: int
    n_dropped_duplicate: int
    n_dropped_incomplete: int
    n_dropped_overlap: int

    @property
    def n_used(self) -> int:
        return int(len(self.positions))

    @property
    def pre(self) -> int:
        return -int(self.relative_days[0])

    @property
    def post(self) -> int:
        return int(self.relative_days[-1])


@dataclass(frozen=True)
class CarResult:
    start: int
    end: int
    n_events: int
    mean_car: float
    se: float
    t_stat: float
    p_value: float
    bmp_t: float
    bmp_p: float
    positive_share: float


@dataclass(frozen=True, eq=False)
class AveragePath:
    relative_days: IntArray
    mean_ar: FloatArray
    se_ar: FloatArray
    t_ar: FloatArray
    mean_car: FloatArray  # cumulated from the first day of the window
    ci_low: FloatArray
    ci_high: FloatArray
    n_events: int
    level: float


@dataclass(frozen=True, eq=False)
class PermutationResult:
    observed: float
    null_mean: float
    null_std: float
    p_value: float
    n_events: int
    n_permutations: int
    alternative: str
    null: FloatArray


def _prepare(
    abnormal: pd.Series[float], sigma: pd.Series[float]
) -> tuple[FloatArray, FloatArray, IntArray]:
    index = abnormal.index
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("abnormal returns need a DatetimeIndex")
    if index.tz is not None:
        raise ValueError("the index must be timezone-naive")
    if len(index) == 0:
        raise ValueError("no data")
    if not index.is_monotonic_increasing or not index.is_unique:
        raise ValueError("the index must be sorted and unique")
    if not abnormal.index.equals(sigma.index):
        raise ValueError("abnormal and sigma must share the same index")

    values = np.asarray(abnormal, dtype=np.float64)
    sig = np.asarray(sigma, dtype=np.float64)
    days = index.to_numpy().astype("datetime64[D]").astype(np.int64)
    return values, sig, days


def _to_day_numbers(events: Sequence[pd.Timestamp]) -> IntArray:
    days: list[int] = []
    for event in events:
        ts = pd.Timestamp(event)
        if pd.isna(ts):
            raise ValueError("event dates must not be missing")
        if ts.tzinfo is not None:
            raise ValueError("event dates must be timezone-naive")
        days.append(int(ts.to_datetime64().astype("datetime64[D]").astype(np.int64)))
    return np.asarray(days, dtype=np.int64)


def _window_valid(values: FloatArray, sigma: FloatArray, pre: int, post: int) -> BoolArray:
    """True where the whole window [t - pre, t + post] lies inside the data and every
    abnormal return is finite with a finite, positive volatility forecast."""
    n = len(values)
    with np.errstate(invalid="ignore"):
        good = np.isfinite(values) & np.isfinite(sigma) & (sigma > 0.0)
    bad_prefix = np.concatenate([[0], np.cumsum(~good)])

    valid = np.zeros(n, dtype=np.bool_)
    centers = np.arange(n)
    lo = centers - pre
    hi = centers + post
    inside = np.nonzero((lo >= 0) & (hi <= n - 1))[0]
    valid[inside] = (bad_prefix[hi[inside] + 1] - bad_prefix[lo[inside]]) == 0
    return valid


def _car_all(values: FloatArray, start: int, end: int) -> FloatArray:
    """Cumulative abnormal return over [t + start, t + end] for every t (NaN if outside)."""
    n = len(values)
    cumulative = np.concatenate([[0.0], np.cumsum(np.nan_to_num(values, nan=0.0))])
    out = np.full(n, np.nan, dtype=np.float64)
    centers = np.arange(n)
    lo = centers + start
    hi = centers + end
    ok = (lo >= 0) & (hi <= n - 1)
    out[ok] = cumulative[hi[ok] + 1] - cumulative[lo[ok]]
    return out


def _t_test(mean: float, se: float, df: int) -> tuple[float, float]:
    if not np.isfinite(se) or se <= 0.0:
        return float("nan"), float("nan")
    stat = mean / se
    return float(stat), float(2.0 * student_t.sf(abs(stat), df))


def build_event_windows(
    abnormal: pd.Series[float],
    sigma: pd.Series[float],
    events: Sequence[pd.Timestamp],
    pre: int,
    post: int,
    non_overlapping: bool = True,
    max_snap_days: int = MAX_SNAP_DAYS,
) -> EventWindows:
    """Cut a window of abnormal returns around each event.

    Events are mapped to the first trading day on or after their date (at most
    `max_snap_days` calendar days later). Duplicates count once; windows that are not
    fully inside the data, or contain a missing return or volatility, are dropped; and,
    if `non_overlapping`, so are events whose windows overlap an earlier kept event.
    """
    if pre < 0 or post < 0:
        raise ValueError("pre and post must be non-negative")
    if max_snap_days < 0:
        raise ValueError("max_snap_days must be non-negative")

    values, sig, index_days = _prepare(abnormal, sigma)
    n = len(values)

    event_days = _to_day_numbers(events)
    pos = np.searchsorted(index_days, event_days, side="left")
    safe = np.minimum(pos, n - 1)
    gap = index_days[safe] - event_days
    mapped = (pos < n) & (gap <= max_snap_days)
    n_unmapped = int(np.sum(~mapped))

    mapped_pos = pos[mapped]
    unique = np.unique(mapped_pos)
    n_duplicate = int(len(mapped_pos) - len(unique))

    valid = _window_valid(values, sig, pre, post)
    complete = unique[valid[unique]]
    n_incomplete = int(len(unique) - len(complete))

    if non_overlapping:
        kept: list[int] = []
        for p in complete.tolist():
            if not kept or int(p) - kept[-1] > pre + post:
                kept.append(int(p))
        final = np.asarray(kept, dtype=np.int64)
    else:
        final = complete.astype(np.int64)
    n_overlap = int(len(complete) - len(final))

    offsets = np.arange(-pre, post + 1, dtype=np.int64)
    rows = final[:, None] + offsets[None, :]
    return EventWindows(
        relative_days=offsets,
        positions=final,
        dates=tuple(pd.Timestamp(abnormal.index[int(p)]) for p in final),
        abnormal=values[rows],
        sigma=sig[rows],
        n_requested=len(events),
        n_dropped_unmapped=n_unmapped,
        n_dropped_duplicate=n_duplicate,
        n_dropped_incomplete=n_incomplete,
        n_dropped_overlap=n_overlap,
    )


def _require_events(windows: EventWindows) -> int:
    n = windows.n_used
    if n < MIN_EVENTS:
        raise ValueError(f"need at least {MIN_EVENTS} events, got {n}")
    return n


def _check_range(windows: EventWindows, start: int, end: int) -> None:
    if not -windows.pre <= start <= end <= windows.post:
        raise ValueError("need -pre <= start <= end <= post")


def car_test(windows: EventWindows, start: int, end: int) -> CarResult:
    """Test whether the cumulative abnormal return over relative days [start, end] is zero.

    Two statistics, both using the cross-sectional spread across events:
    - t: mean CAR over its standard error.
    - BMP (Boehmer, Musumeci and Poulsen): each event's CAR is first divided by the
      volatility forecast for its own window, so noisy events do not dominate.
    """
    n = _require_events(windows)
    _check_range(windows, start, end)

    cols = slice(start + windows.pre, end + windows.pre + 1)
    car = windows.abnormal[:, cols].sum(axis=1)
    variance = (windows.sigma[:, cols] ** 2).sum(axis=1)
    scar = car / np.sqrt(variance)

    se = float(car.std(ddof=1)) / math.sqrt(n)
    t_stat, p_value = _t_test(float(car.mean()), se, n - 1)
    bmp_se = float(scar.std(ddof=1)) / math.sqrt(n)
    bmp_t, bmp_p = _t_test(float(scar.mean()), bmp_se, n - 1)

    return CarResult(
        start=start,
        end=end,
        n_events=n,
        mean_car=float(car.mean()),
        se=se,
        t_stat=t_stat,
        p_value=p_value,
        bmp_t=bmp_t,
        bmp_p=bmp_p,
        positive_share=float(np.mean(car > 0.0)),
    )


def average_path(windows: EventWindows, level: float = DEFAULT_LEVEL) -> AveragePath:
    """Average abnormal return and cumulative abnormal return by relative day."""
    n = _require_events(windows)
    if not 0.0 < level < 1.0:
        raise ValueError("level must be between 0 and 1")

    ar = windows.abnormal
    mean_ar = ar.mean(axis=0)
    se_ar = ar.std(axis=0, ddof=1) / math.sqrt(n)
    with np.errstate(divide="ignore", invalid="ignore"):
        t_ar = mean_ar / se_ar

    car = np.cumsum(ar, axis=1)
    mean_car = car.mean(axis=0)
    se_car = car.std(axis=0, ddof=1) / math.sqrt(n)
    critical = float(student_t.ppf(0.5 + level / 2.0, n - 1))

    return AveragePath(
        relative_days=windows.relative_days,
        mean_ar=mean_ar,
        se_ar=se_ar,
        t_ar=t_ar,
        mean_car=mean_car,
        ci_low=mean_car - critical * se_car,
        ci_high=mean_car + critical * se_car,
        n_events=n,
        level=level,
    )


def permutation_test(
    abnormal: pd.Series[float],
    sigma: pd.Series[float],
    windows: EventWindows,
    start: int,
    end: int,
    n_perm: int = DEFAULT_N_PERM,
    alternative: Alternative = "two-sided",
    seed: int = 0,
) -> PermutationResult:
    """Compare the mean CAR after the real events with the same number of random dates.

    Random dates are drawn without replacement from every day that satisfies the same
    window requirements as the real events. The p-value is (1 + extreme) / (n_perm + 1),
    so it is never exactly zero.
    """
    if n_perm < MIN_PERM:
        raise ValueError(f"n_perm must be at least {MIN_PERM}")
    if alternative not in ("two-sided", "greater", "less"):
        raise ValueError("alternative must be 'two-sided', 'greater' or 'less'")
    k = windows.n_used
    if k < 1:
        raise ValueError("need at least 1 event")
    _check_range(windows, start, end)

    values, sig, _ = _prepare(abnormal, sigma)
    if int(windows.positions.max()) >= len(values):
        raise ValueError("windows do not match this series")

    car_all = _car_all(values, start, end)
    eligible = np.nonzero(_window_valid(values, sig, windows.pre, windows.post))[0]
    observed = float(car_all[windows.positions].mean())

    rng = np.random.default_rng(seed)
    null = np.empty(n_perm, dtype=np.float64)
    for b in range(n_perm):
        draw = rng.choice(eligible, size=k, replace=False)
        null[b] = float(car_all[draw].mean())

    if alternative == "two-sided":
        extreme = np.abs(null) >= abs(observed)
    elif alternative == "greater":
        extreme = null >= observed
    else:
        extreme = null <= observed

    return PermutationResult(
        observed=observed,
        null_mean=float(null.mean()),
        null_std=float(null.std(ddof=1)),
        p_value=(1.0 + float(np.sum(extreme))) / (n_perm + 1.0),
        n_events=k,
        n_permutations=n_perm,
        alternative=alternative,
        null=null,
    )
