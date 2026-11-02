from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from whymoved.domain.scoring import (
    empirical_tail_probability,
    ewma_volatility,
    expected_false_alarms,
    gaussian_two_sided_p,
    score_unexplained,
    standardized_scores,
)


def _index(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2015-01-01", periods=n)


def test_ewma_tracks_a_volatility_regime_change() -> None:
    rng = np.random.default_rng(1)
    resid = np.concatenate([rng.normal(0, 0.01, 300), rng.normal(0, 0.03, 300)])

    sigma = ewma_volatility(resid)

    assert float(np.mean(sigma[100:290])) == pytest.approx(0.01, rel=0.15)
    assert float(np.mean(sigma[400:600])) == pytest.approx(0.03, rel=0.15)


def test_ewma_ignores_the_current_and_future_days() -> None:
    rng = np.random.default_rng(2)
    resid = rng.normal(0, 0.01, 400)
    changed = resid.copy()
    changed[200:] *= 25.0

    before = ewma_volatility(resid)
    after = ewma_volatility(changed)

    assert np.allclose(before[:201], after[:201], equal_nan=True)
    assert not np.isclose(before[250], after[250])


def test_ewma_is_nan_during_warmup() -> None:
    rng = np.random.default_rng(3)

    sigma = ewma_volatility(rng.normal(0, 0.01, 100), warmup=30)

    assert np.isnan(sigma[:30]).all()
    assert np.isfinite(sigma[30:]).all()


def test_short_series_scores_without_crashing() -> None:
    rng = np.random.default_rng(3)
    series = pd.Series(rng.normal(0, 0.01, 20), index=_index(20))

    scored = score_unexplained(series)

    assert scored["sigma"].isna().all()
    assert scored["p_value"].isna().all()
    assert not scored["flagged"].any()


def test_standardized_scores_have_unit_scale_for_iid_noise() -> None:
    rng = np.random.default_rng(4)

    z = standardized_scores(rng.normal(0, 0.01, 3000))

    assert 0.9 < float(np.nanstd(z)) < 1.2


def test_flag_rate_matches_alpha_for_iid_noise() -> None:
    rng = np.random.default_rng(5)
    series = pd.Series(rng.normal(0, 0.01, 3000), index=_index(3000))

    scored = score_unexplained(series, alpha=0.05)

    eligible = scored["p_value"].notna()
    flags = int(scored.loc[eligible, "flagged"].sum())
    expected = expected_false_alarms(scored, 0.05)
    assert 0.03 < flags / int(eligible.sum()) < 0.07
    assert abs(flags - expected) < 0.4 * expected


def test_planted_shock_is_flagged_with_the_minimum_p_value() -> None:
    rng = np.random.default_rng(6)
    resid = rng.normal(0, 0.01, 600)
    resid[400] = 0.10  # roughly a 10-sigma move
    scored = score_unexplained(pd.Series(resid, index=_index(600)))

    row = scored.iloc[400]

    assert bool(row["flagged"]) is True
    assert row["p_value"] == pytest.approx(1.0 / 251.0)
    assert row["return_period"] == pytest.approx(251.0)


def test_zero_residuals_give_nan_not_infinity() -> None:
    scored = score_unexplained(pd.Series(np.zeros(200), index=_index(200)))

    assert scored["z"].isna().all()
    assert scored["p_value"].isna().all()
    assert not scored["flagged"].any()


def test_non_finite_residuals_are_rejected() -> None:
    resid = np.array([0.01] * 40 + [np.nan])

    with pytest.raises(ValueError, match="NaN"):
        ewma_volatility(resid)


def test_expected_false_alarms_counts_only_scored_days() -> None:
    frame = pd.DataFrame({"p_value": [np.nan, 0.5, 0.1, np.nan]})

    assert expected_false_alarms(frame, 0.05) == pytest.approx(0.1)


def test_gaussian_p_value_matches_the_textbook() -> None:
    p = gaussian_two_sided_p(np.array([1.959964, 0.0]))

    assert p[0] == pytest.approx(0.05, abs=1e-4)
    assert p[1] == pytest.approx(1.0)


def test_empirical_p_rejects_bad_parameters() -> None:
    with pytest.raises(ValueError, match="min_history"):
        empirical_tail_probability(np.zeros(10), lookback=5, min_history=10)


def test_invalid_alpha_and_lambda_are_rejected() -> None:
    series = pd.Series(np.zeros(100), index=_index(100))

    with pytest.raises(ValueError, match="alpha"):
        score_unexplained(series, alpha=0.0)
    with pytest.raises(ValueError, match="lam"):
        score_unexplained(series, lam=1.0)
