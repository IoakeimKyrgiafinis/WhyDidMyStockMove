"""Event study pooled across many stocks, with events clustered by calendar date.

One stock has only a handful of insider purchases, so per-stock tests have little power.
Pooling the whole universe fixes that, but events on the same date share the same market
shocks and are not independent. The standard fix is to average the abnormal returns of all
events that share a date first, and test across dates: each date counts once.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.stats import t as student_t

from whymoved.domain.events import DEFAULT_LEVEL, EventWindows

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

MIN_CLUSTERS = 2


@dataclass(frozen=True)
class WindowTest:
    start: int
    end: int
    mean_car: float
    se: float
    t_stat: float
    p_value: float
    ci_low: float
    ci_high: float
    positive_share: float  # share of date clusters with a positive CAR


@dataclass(frozen=True, eq=False)
class PooledStudy:
    relative_days: IntArray
    mean_car: FloatArray  # cumulated from the first day of the window
    ci_low: FloatArray
    ci_high: FloatArray
    tests: tuple[WindowTest, ...]
    n_events: int
    n_stocks: int
    n_clusters: int
    level: float


def pool_event_windows(
    windows: Sequence[EventWindows],
    tests: Sequence[tuple[int, int]],
    level: float = DEFAULT_LEVEL,
) -> PooledStudy:
    """Pool event windows from many stocks and test cumulative abnormal returns.

    All windows must share the same relative days. Each test (start, end) is a CAR over
    relative days start..end, tested across date clusters with a t-test.
    """
    used = [w for w in windows if w.n_used > 0]
    if not used:
        raise ValueError("no events to pool")
    days = used[0].relative_days
    if any(not np.array_equal(w.relative_days, days) for w in used):
        raise ValueError("all windows must cover the same relative days")
    if not 0.0 < level < 1.0:
        raise ValueError("level must be between 0 and 1")

    abnormal = np.vstack([w.abnormal for w in used])
    dates = np.array([d.value for w in used for d in w.dates], dtype=np.int64)
    clusters, inverse = np.unique(dates, return_inverse=True)
    k = len(clusters)
    if k < MIN_CLUSTERS:
        raise ValueError(f"need events on at least {MIN_CLUSTERS} different dates, got {k}")

    sums = np.zeros((k, abnormal.shape[1]), dtype=np.float64)
    np.add.at(sums, inverse, abnormal)
    counts = np.bincount(inverse, minlength=k).astype(np.float64)
    cluster_ar = sums / counts[:, None]

    critical = float(student_t.ppf(0.5 + level / 2.0, k - 1))
    car_path = np.cumsum(cluster_ar, axis=1)
    mean_path = car_path.mean(axis=0)
    se_path = car_path.std(axis=0, ddof=1) / math.sqrt(k)

    pre = -int(days[0])
    results: list[WindowTest] = []
    for start, end in tests:
        if not int(days[0]) <= start <= end <= int(days[-1]):
            raise ValueError("test windows must lie inside the event window")
        car = cluster_ar[:, start + pre : end + pre + 1].sum(axis=1)
        mean = float(car.mean())
        se = float(car.std(ddof=1)) / math.sqrt(k)
        t_stat = mean / se if se > 0 else float("nan")
        p = float(2.0 * student_t.sf(abs(t_stat), k - 1)) if se > 0 else float("nan")
        results.append(
            WindowTest(
                start=start,
                end=end,
                mean_car=mean,
                se=se,
                t_stat=t_stat,
                p_value=p,
                ci_low=mean - critical * se,
                ci_high=mean + critical * se,
                positive_share=float(np.mean(car > 0.0)),
            )
        )

    return PooledStudy(
        relative_days=days,
        mean_car=mean_path,
        ci_low=mean_path - critical * se_path,
        ci_high=mean_path + critical * se_path,
        tests=tuple(results),
        n_events=int(abnormal.shape[0]),
        n_stocks=len(used),
        n_clusters=k,
        level=level,
    )
