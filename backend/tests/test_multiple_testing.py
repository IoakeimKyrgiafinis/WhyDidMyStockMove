from __future__ import annotations

import numpy as np
import pytest
from statsmodels.stats.multitest import multipletests

from whymoved.domain.multiple_testing import benjamini_hochberg


def test_known_example() -> None:
    adjusted, reject = benjamini_hochberg(np.array([0.01, 0.04, 0.03, 0.005]))

    assert adjusted == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert reject.all()


def test_step_up_rescues_larger_p_values() -> None:
    adjusted, reject = benjamini_hochberg(np.array([0.04, 0.041, 0.042]))

    assert adjusted == pytest.approx([0.042, 0.042, 0.042])
    assert reject.all()


def test_only_the_strong_signal_survives() -> None:
    adjusted, reject = benjamini_hochberg(np.array([0.001, 0.2, 0.5]))

    assert adjusted == pytest.approx([0.003, 0.3, 0.5])
    assert reject.tolist() == [True, False, False]


def test_matches_statsmodels() -> None:
    p = np.random.default_rng(0).uniform(size=200) ** 3

    adjusted, reject = benjamini_hochberg(p, q=0.05)
    ref_reject, ref_adjusted, _, _ = multipletests(p, alpha=0.05, method="fdr_bh")

    assert adjusted == pytest.approx(ref_adjusted)
    assert np.array_equal(reject, ref_reject)


def test_adjusted_values_are_monotone_in_the_raw_values() -> None:
    p = np.random.default_rng(1).uniform(size=100)

    adjusted, _ = benjamini_hochberg(p)

    assert (adjusted >= p - 1e-12).all()
    assert (adjusted <= 1.0).all()
    assert (np.diff(adjusted[np.argsort(p)]) >= -1e-12).all()


def test_false_discovery_proportion_is_controlled() -> None:
    rng = np.random.default_rng(1)
    n_null, n_alt, reps = 80, 20, 300
    proportions = []
    for _ in range(reps):
        p = np.concatenate([rng.uniform(size=n_null), rng.beta(0.2, 5.0, size=n_alt)])
        _, reject = benjamini_hochberg(p, q=0.05)
        false = int(reject[:n_null].sum())
        proportions.append(false / max(int(reject.sum()), 1))

    assert float(np.mean(proportions)) < 0.07  # about 0.04 expected


def test_empty_input_is_allowed() -> None:
    adjusted, reject = benjamini_hochberg(np.array([]))

    assert adjusted.shape == (0,)
    assert reject.shape == (0,)


def test_invalid_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        benjamini_hochberg(np.array([0.1, float("nan")]))
    with pytest.raises(ValueError, match="between 0 and 1"):
        benjamini_hochberg(np.array([0.1, 1.5]))
    with pytest.raises(ValueError, match="q must"):
        benjamini_hochberg(np.array([0.1]), q=0.0)
    with pytest.raises(ValueError, match="one-dimensional"):
        benjamini_hochberg(np.zeros((2, 2)))
