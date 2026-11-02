from __future__ import annotations

import numpy as np
import pytest

from helpers import make_panel, true_variance_shares
from whymoved.domain.variance import block_bootstrap_indices, variance_shares, variance_split


def test_shares_sum_to_one() -> None:
    stock, market, sector = make_panel(n=500).arrays()

    shares = variance_shares(stock, market, sector)

    assert float(shares.sum()) == pytest.approx(1.0, abs=1e-9)
    assert (shares >= 0).all()


def test_shares_match_the_planted_truth() -> None:
    stock, market, sector = make_panel(n=4000).arrays()
    expected = np.array(true_variance_shares())

    shares = variance_shares(stock, market, sector)

    assert shares == pytest.approx(expected, abs=0.04)


def test_independent_stock_is_almost_all_idiosyncratic() -> None:
    _, market, sector = make_panel(n=2000).arrays()
    stock = np.random.default_rng(99).normal(0.0, 0.01, 2000)

    shares = variance_shares(stock, market, sector)

    assert shares[2] > 0.98


def test_block_bootstrap_indices_are_contiguous_blocks() -> None:
    rng = np.random.default_rng(0)

    idx = block_bootstrap_indices(23, 5, 40, rng)

    assert idx.shape == (40, 23)
    assert idx.min() >= 0
    assert idx.max() <= 22
    steps = np.diff(idx, axis=1)
    within_block = np.array([(k + 1) % 5 != 0 for k in range(22)])
    assert (steps[:, within_block] == 1).all()


def test_bootstrap_is_reproducible_for_a_seed() -> None:
    stock, market, sector = make_panel(n=300).arrays()

    a = variance_split(stock, market, sector, n_boot=200, seed=5)
    b = variance_split(stock, market, sector, n_boot=200, seed=5)
    c = variance_split(stock, market, sector, n_boot=200, seed=6)

    assert np.array_equal(a.ci_low, b.ci_low)
    assert not np.array_equal(a.ci_low, c.ci_low)


def test_interval_contains_the_point_estimate() -> None:
    stock, market, sector = make_panel(n=500).arrays()

    split = variance_split(stock, market, sector, n_boot=500)

    assert (split.ci_low <= split.shares).all()
    assert (split.shares <= split.ci_high).all()
    assert split.market + split.sector + split.idiosyncratic == pytest.approx(1.0)


def test_interval_narrows_as_the_sample_grows() -> None:
    small = variance_split(*make_panel(n=250).arrays(), n_boot=300)
    large = variance_split(*make_panel(n=2000).arrays(), n_boot=300)

    small_width = float(small.ci_high[0] - small.ci_low[0])
    large_width = float(large.ci_high[0] - large.ci_low[0])

    assert small_width > 1.5 * large_width


def test_interval_covers_the_planted_truth_most_of_the_time() -> None:
    """The slowest test: 30 synthetic panels, each bootstrapped 150 times."""
    truth = true_variance_shares()[0]
    reps = 30
    hits = 0
    for seed in range(reps):
        stock, market, sector = make_panel(n=500, seed=seed).arrays()
        split = variance_split(stock, market, sector, n_boot=150, seed=seed)
        if split.ci_low[0] <= truth <= split.ci_high[0]:
            hits += 1

    assert hits >= 24  # nominal 95 percent, so about 28 expected


def test_invalid_inputs_are_rejected() -> None:
    stock, market, sector = make_panel(n=200).arrays()

    with pytest.raises(ValueError, match="same length"):
        variance_shares(stock[:-1], market, sector)
    with pytest.raises(ValueError, match="at least"):
        variance_shares(stock[:30], market[:30], sector[:30])
    with pytest.raises(ValueError, match="zero variance"):
        variance_shares(np.zeros(200), market, sector)
    with pytest.raises(ValueError, match="block"):
        variance_split(stock, market, sector, block=500)
    with pytest.raises(ValueError, match="level"):
        variance_split(stock, market, sector, level=1.0)
    with pytest.raises(ValueError, match="n_boot"):
        variance_split(stock, market, sector, n_boot=10)
