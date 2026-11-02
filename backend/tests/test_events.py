from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import ttest_1samp

from whymoved.domain.events import (
    average_path,
    build_event_windows,
    car_test,
    permutation_test,
)
from whymoved.domain.scoring import score_unexplained

NOISE = 0.01
PRE = 5
POST = 5
POSITIONS = list(range(100, 1400, 30))  # 44 events, well apart


def make_abnormal(n: int = 1500, seed: int = 0) -> tuple[pd.Series[float], pd.Series[float]]:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2015-01-01", periods=n)
    values = rng.normal(0.0, NOISE, n)
    return pd.Series(values, index=index), pd.Series(np.full(n, NOISE), index=index)


def plant(abnormal: pd.Series[float], positions: list[int], effect: float) -> pd.Series[float]:
    values = abnormal.to_numpy().copy()
    values[positions] += effect
    return pd.Series(values, index=abnormal.index)


def dates_at(abnormal: pd.Series[float], positions: list[int]) -> list[pd.Timestamp]:
    return [pd.Timestamp(abnormal.index[p]) for p in positions]


def first_friday(abnormal: pd.Series[float]) -> int:
    return next(i for i in range(100, 200) if abnormal.index[i].weekday() == 4)


# ---- building windows ----


def test_windows_recover_the_planted_effect_at_day_zero() -> None:
    abnormal, sigma = make_abnormal()
    planted = plant(abnormal, POSITIONS, 0.03)

    windows = build_event_windows(planted, sigma, dates_at(planted, POSITIONS), PRE, POST)
    path = average_path(windows)

    assert windows.n_used == len(POSITIONS)
    assert windows.abnormal.shape == (len(POSITIONS), PRE + POST + 1)
    assert windows.relative_days.tolist() == list(range(-PRE, POST + 1))
    assert abs(path.mean_ar[PRE] - 0.03) < 0.006
    others = np.delete(path.mean_ar, PRE)
    assert float(np.max(np.abs(others))) < 0.006


def test_event_on_a_weekend_snaps_to_the_next_trading_day() -> None:
    abnormal, sigma = make_abnormal()
    friday = first_friday(abnormal)
    saturday = pd.Timestamp(abnormal.index[friday]) + pd.Timedelta(days=1)

    windows = build_event_windows(abnormal, sigma, [saturday], PRE, POST)

    assert windows.positions.tolist() == [friday + 1]
    assert windows.n_dropped_unmapped == 0


def test_events_outside_the_data_are_dropped() -> None:
    abnormal, sigma = make_abnormal(n=400)
    events = [
        pd.Timestamp("2000-01-03"),
        pd.Timestamp("2030-01-03"),
        pd.Timestamp(abnormal.index[200]),
    ]

    windows = build_event_windows(abnormal, sigma, events, PRE, POST)

    assert windows.n_requested == 3
    assert windows.n_used == 1
    assert windows.n_dropped_unmapped == 2


def test_incomplete_windows_are_dropped() -> None:
    abnormal, sigma = make_abnormal(n=400)
    sigma_values = sigma.to_numpy().copy()
    sigma_values[:50] = np.nan  # volatility warm-up
    sigma = pd.Series(sigma_values, index=abnormal.index)
    events = dates_at(abnormal, [2, 30, 200, 398])

    windows = build_event_windows(abnormal, sigma, events, PRE, POST)

    assert windows.positions.tolist() == [200]
    assert windows.n_dropped_incomplete == 3


def test_duplicate_events_are_counted_once() -> None:
    abnormal, sigma = make_abnormal()
    friday = first_friday(abnormal)
    base = pd.Timestamp(abnormal.index[friday])
    events = [base + pd.Timedelta(days=1), base + pd.Timedelta(days=2)]  # Saturday, Sunday

    windows = build_event_windows(abnormal, sigma, events, PRE, POST)

    assert windows.positions.tolist() == [friday + 1]
    assert windows.n_dropped_duplicate == 1


def test_overlapping_events_are_dropped_unless_allowed() -> None:
    abnormal, sigma = make_abnormal()
    events = dates_at(abnormal, [200, 203, 230])

    strict = build_event_windows(abnormal, sigma, events, PRE, POST)
    loose = build_event_windows(abnormal, sigma, events, PRE, POST, non_overlapping=False)

    assert strict.positions.tolist() == [200, 230]
    assert strict.n_dropped_overlap == 1
    assert loose.positions.tolist() == [200, 203, 230]


# ---- CAR tests ----


def test_car_test_detects_a_planted_effect() -> None:
    abnormal, sigma = make_abnormal()
    planted = plant(abnormal, POSITIONS, 0.03)
    windows = build_event_windows(planted, sigma, dates_at(planted, POSITIONS), PRE, POST)

    result = car_test(windows, 0, 0)

    assert result.mean_car == pytest.approx(0.03, abs=0.006)
    assert result.t_stat > 10
    assert result.p_value < 1e-6
    assert result.bmp_t > 10
    assert result.positive_share > 0.9


def test_car_test_does_not_reject_pure_noise() -> None:
    abnormal, sigma = make_abnormal()
    windows = build_event_windows(abnormal, sigma, dates_at(abnormal, POSITIONS), PRE, POST)

    result = car_test(windows, 0, 2)

    assert abs(result.t_stat) < 3.5
    assert abs(result.bmp_t) < 3.5
    assert result.p_value > 1e-3


def test_car_t_stat_matches_scipy() -> None:
    abnormal, sigma = make_abnormal()
    planted = plant(abnormal, POSITIONS, 0.004)
    windows = build_event_windows(planted, sigma, dates_at(planted, POSITIONS), PRE, POST)

    result = car_test(windows, 0, 2)
    cars = windows.abnormal[:, PRE : PRE + 3].sum(axis=1)
    reference = ttest_1samp(cars, 0.0)

    assert result.t_stat == pytest.approx(float(reference.statistic), rel=1e-9)
    assert result.p_value == pytest.approx(float(reference.pvalue), rel=1e-9)


def test_bmp_statistic_is_scale_free() -> None:
    abnormal, sigma = make_abnormal()
    planted = plant(abnormal, POSITIONS, 0.02)
    scaled = planted * 10.0
    events = dates_at(planted, POSITIONS)

    base = car_test(build_event_windows(planted, sigma, events, PRE, POST), 0, 1)
    big = car_test(build_event_windows(scaled, sigma * 10.0, events, PRE, POST), 0, 1)

    assert big.mean_car == pytest.approx(10.0 * base.mean_car)
    assert big.bmp_t == pytest.approx(base.bmp_t)
    assert big.t_stat == pytest.approx(base.t_stat)


def test_average_path_is_consistent() -> None:
    abnormal, sigma = make_abnormal()
    planted = plant(abnormal, POSITIONS, 0.03)
    windows = build_event_windows(planted, sigma, dates_at(planted, POSITIONS), PRE, POST)

    path = average_path(windows)

    assert path.mean_car[-1] == pytest.approx(float(path.mean_ar.sum()))
    assert (path.ci_low <= path.mean_car).all()
    assert (path.mean_car <= path.ci_high).all()
    assert path.t_ar[PRE] > 10
    assert path.mean_car[-1] > 0.02


def test_works_with_ewma_sigma_from_scoring() -> None:
    abnormal, _ = make_abnormal(seed=3)
    planted = plant(abnormal, POSITIONS, 0.05)
    scored = score_unexplained(planted)
    events = dates_at(planted, [10, *POSITIONS])  # position 10 is inside the warm-up

    windows = build_event_windows(planted, scored["sigma"], events, PRE, POST)
    result = car_test(windows, 0, 0)

    assert windows.n_dropped_incomplete == 1
    assert windows.n_used == len(POSITIONS)
    assert result.bmp_t > 10


# ---- permutation test ----


def test_permutation_detects_a_planted_effect() -> None:
    abnormal, sigma = make_abnormal()
    planted = plant(abnormal, POSITIONS, 0.03)
    windows = build_event_windows(planted, sigma, dates_at(planted, POSITIONS), PRE, POST)

    both = permutation_test(planted, sigma, windows, 0, 0, n_perm=2000)
    greater = permutation_test(planted, sigma, windows, 0, 0, n_perm=2000, alternative="greater")
    less = permutation_test(planted, sigma, windows, 0, 0, n_perm=2000, alternative="less")

    assert both.observed == pytest.approx(0.03, abs=0.006)
    assert both.n_events == len(POSITIONS)
    assert both.p_value <= 0.01
    assert greater.p_value <= 0.01
    assert less.p_value > 0.99
    assert abs(both.null_mean) < 0.002


def test_permutation_size_is_controlled() -> None:
    positions = list(range(100, 500, 20))
    rejections = 0
    for seed in range(40):
        abnormal, sigma = make_abnormal(n=600, seed=seed)
        windows = build_event_windows(abnormal, sigma, dates_at(abnormal, positions), PRE, POST)
        result = permutation_test(abnormal, sigma, windows, 0, 0, n_perm=200, seed=seed)
        if result.p_value <= 0.10:
            rejections += 1

    assert rejections <= 12  # nominal 10 percent of 40 is about 4


def test_permutation_is_reproducible_for_a_seed() -> None:
    abnormal, sigma = make_abnormal()
    windows = build_event_windows(abnormal, sigma, dates_at(abnormal, POSITIONS), PRE, POST)

    a = permutation_test(abnormal, sigma, windows, 0, 1, n_perm=300, seed=1)
    b = permutation_test(abnormal, sigma, windows, 0, 1, n_perm=300, seed=1)
    c = permutation_test(abnormal, sigma, windows, 0, 1, n_perm=300, seed=2)

    assert np.array_equal(a.null, b.null)
    assert not np.array_equal(a.null, c.null)


# ---- validation ----


def test_invalid_window_inputs_are_rejected() -> None:
    abnormal, sigma = make_abnormal(n=400)
    event = [pd.Timestamp(abnormal.index[200])]

    with pytest.raises(ValueError, match="non-negative"):
        build_event_windows(abnormal, sigma, event, -1, POST)
    with pytest.raises(ValueError, match="sorted"):
        build_event_windows(abnormal.iloc[::-1], sigma.iloc[::-1], event, PRE, POST)
    with pytest.raises(ValueError, match="same index"):
        build_event_windows(abnormal, sigma.iloc[1:], event, PRE, POST)
    with pytest.raises(ValueError, match="timezone"):
        build_event_windows(abnormal, sigma, [pd.Timestamp("2016-03-01", tz="UTC")], PRE, POST)
    with pytest.raises(ValueError, match="DatetimeIndex"):
        build_event_windows(pd.Series(np.zeros(50)), pd.Series(np.ones(50)), event, PRE, POST)


def test_invalid_test_inputs_are_rejected() -> None:
    abnormal, sigma = make_abnormal()
    one = build_event_windows(abnormal, sigma, dates_at(abnormal, [200]), PRE, POST)
    many = build_event_windows(abnormal, sigma, dates_at(abnormal, POSITIONS), PRE, POST)

    with pytest.raises(ValueError, match="at least"):
        car_test(one, 0, 0)
    with pytest.raises(ValueError, match="start"):
        car_test(many, 2, 1)
    with pytest.raises(ValueError, match="start"):
        car_test(many, -9, 0)
    with pytest.raises(ValueError, match="n_perm"):
        permutation_test(abnormal, sigma, many, 0, 0, n_perm=10)
    with pytest.raises(ValueError, match="alternative"):
        permutation_test(
            abnormal,
            sigma,
            many,
            0,
            0,
            n_perm=200,
            alternative="sideways",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="level"):
        average_path(many, level=1.5)
