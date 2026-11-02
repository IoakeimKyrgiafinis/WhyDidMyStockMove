from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from whymoved.domain.reaction import event_positions, reaction_size


def z_series(n: int = 400, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.0, 1.0, n), index=pd.bdate_range("2024-01-01", periods=n))


def test_big_event_days_are_detected() -> None:
    z = z_series()
    days = [60, 120, 180, 240, 300]
    for k, p in enumerate(days):
        z.iloc[p] = 5.0 if k % 2 else -5.0  # big moves in both directions

    result = reaction_size(z, [z.index[p] for p in days], n_perm=1000)

    assert result is not None
    assert result.n_events == 5
    assert result.ratio > 4.0
    assert result.p_value < 0.01


def test_ordinary_days_are_not_special() -> None:
    z = z_series(seed=3)

    result = reaction_size(z, [z.index[p] for p in (50, 150, 250, 350)], n_perm=1000)

    assert result is not None
    assert result.p_value > 0.01


def test_weekend_events_snap_to_monday_and_unscored_days_are_ignored() -> None:
    z = z_series()
    z.iloc[:10] = np.nan
    saturday = pd.Timestamp("2024-01-06")

    pos = event_positions(pd.DatetimeIndex(z.index), [saturday, z.index[2]])

    assert list(pos) == [2, 5]  # Saturday -> Monday the 8th, the 6th business day
    assert reaction_size(z, [z.index[2], z.index[3]]) is None  # both unscored


def test_fewer_than_two_events_gives_none() -> None:
    z = z_series()

    assert reaction_size(z, [z.index[100]]) is None
    assert reaction_size(z, []) is None


def test_requires_dates() -> None:
    with pytest.raises(ValueError, match="DatetimeIndex"):
        reaction_size(pd.Series([1.0, 2.0, 3.0]), [])
