from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from whymoved.domain.events import build_event_windows
from whymoved.domain.pooled import pool_event_windows


def series(n: int, seed: int, drift_at: list[int], drift: float) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2024-01-01", periods=n)
    values = rng.normal(0.0, 0.01, n)
    for p in drift_at:
        values[p : p + 3] += drift
    return pd.Series(values, index=index), pd.Series(0.01, index=index)


def test_a_planted_drift_is_found_across_stocks() -> None:
    windows = []
    for seed in range(8):
        positions = [40 + 30 * k + seed for k in range(5)]
        abnormal, sigma = series(260, seed, positions, 0.01)
        events = [abnormal.index[p] for p in positions]
        windows.append(build_event_windows(abnormal, sigma, events, 2, 10))

    study = pool_event_windows(windows, [(0, 2), (0, 10)])

    early = study.tests[0]
    assert early.mean_car == pytest.approx(0.03, abs=0.01)
    assert early.p_value < 0.001
    assert early.ci_low < 0.03 < early.ci_high
    assert study.n_events == 40
    assert study.n_stocks == 8
    assert len(study.mean_car) == 13


def test_events_on_the_same_date_count_once() -> None:
    windows = []
    for seed in range(3):
        abnormal, sigma = series(200, seed, [], 0.0)
        events = [abnormal.index[p] for p in (50, 100, 150)]  # the same dates in every stock
        windows.append(build_event_windows(abnormal, sigma, events, 2, 5))

    study = pool_event_windows(windows, [(0, 5)])

    assert study.n_events == 9
    assert study.n_clusters == 3


def test_no_effect_is_usually_not_significant() -> None:
    hits = 0
    for rep in range(40):
        windows = []
        for seed in range(5):
            abnormal, sigma = series(260, 1000 * rep + seed, [], 0.0)
            events = [abnormal.index[p] for p in (40 + seed, 90 + seed, 140 + seed, 190 + seed)]
            windows.append(build_event_windows(abnormal, sigma, events, 2, 10))
        if pool_event_windows(windows, [(0, 10)]).tests[0].p_value < 0.05:
            hits += 1

    assert hits <= 6  # about 2 expected at the 5% level


def test_bad_inputs_are_rejected() -> None:
    abnormal, sigma = series(200, 0, [], 0.0)
    one = build_event_windows(abnormal, sigma, [abnormal.index[50]], 2, 5)
    other = build_event_windows(abnormal, sigma, [abnormal.index[90]], 3, 5)

    with pytest.raises(ValueError, match="no events"):
        pool_event_windows([], [(0, 1)])
    with pytest.raises(ValueError, match="same relative days"):
        pool_event_windows([one, other], [(0, 1)])
    with pytest.raises(ValueError, match="at least 2"):
        pool_event_windows([one], [(0, 1)])
    with pytest.raises(ValueError, match="inside"):
        pool_event_windows(
            [one, build_event_windows(abnormal, sigma, [abnormal.index[90]], 2, 5)], [(0, 9)]
        )
