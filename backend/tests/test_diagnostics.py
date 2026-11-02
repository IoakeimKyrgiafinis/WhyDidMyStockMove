from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest
from statsmodels.stats.diagnostic import acorr_ljungbox, het_arch
from statsmodels.stats.stattools import jarque_bera

from helpers import MARKET_STD, NOISE_STD, PURE_SECTOR_STD, TRUE_BETA_MARKET, make_panel
from whymoved.domain.diagnostics import (
    BETA_Z_CAUTION,
    R2_CAUTION,
    arch_lm_test,
    assess_model_health,
    beta_stability_z,
    classify_health,
    jarque_bera_test,
    ljung_box_test,
)

FloatArray = npt.NDArray[np.float64]


def simulate_arch(n: int, seed: int, alpha: float = 0.5) -> FloatArray:
    rng = np.random.default_rng(seed)
    shocks = rng.standard_normal(n)
    out = np.zeros(n)
    for t in range(1, n):
        out[t] = np.sqrt(0.5 + alpha * out[t - 1] ** 2) * shocks[t]
    return out


def simulate_ar1(n: int, seed: int, phi: float) -> FloatArray:
    rng = np.random.default_rng(seed)
    shocks = rng.standard_normal(n)
    out = np.zeros(n)
    for t in range(1, n):
        out[t] = phi * out[t - 1] + shocks[t]
    return out


def factors(n: int, seed: int) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Market, pure sector and the resulting sector return."""
    rng = np.random.default_rng(seed)
    market = rng.normal(0.0004, MARKET_STD, n)
    pure = rng.normal(0.0, PURE_SECTOR_STD, n)
    sector = 0.0001 + 0.8 * market + pure
    return market, pure, sector


# ---- cross-checks against a reference implementation ----


def test_jarque_bera_matches_statsmodels() -> None:
    x = np.random.default_rng(1).standard_t(4, 1500)

    mine = jarque_bera_test(x)
    jb, p_value, skew, kurt = jarque_bera(x)

    assert mine.statistic == pytest.approx(float(jb), rel=1e-6)
    assert mine.p_value == pytest.approx(float(p_value), rel=1e-6, abs=1e-12)
    assert mine.skewness == pytest.approx(float(skew), rel=1e-6)
    assert mine.excess_kurtosis + 3.0 == pytest.approx(float(kurt), rel=1e-6)


def test_ljung_box_matches_statsmodels() -> None:
    base = np.random.default_rng(2).standard_normal(501)
    x = base[1:] + 0.2 * base[:-1]

    mine = ljung_box_test(x, lags=10)
    ref = acorr_ljungbox(x, lags=[10], return_df=True)

    assert mine.statistic == pytest.approx(float(ref["lb_stat"].iloc[0]), rel=1e-6)
    assert mine.p_value == pytest.approx(float(ref["lb_pvalue"].iloc[0]), rel=1e-6, abs=1e-12)


def test_arch_lm_matches_statsmodels() -> None:
    x = simulate_arch(1500, seed=3)

    mine = arch_lm_test(x, lags=5)
    lm, lm_p, _, _ = het_arch(x, 5)

    assert mine.statistic == pytest.approx(float(lm), rel=1e-6)
    assert mine.p_value == pytest.approx(float(lm_p), rel=1e-6, abs=1e-12)


# ---- size and power ----


def test_white_noise_is_not_rejected() -> None:
    x = np.random.default_rng(10).standard_normal(2000)

    assert ljung_box_test(x).p_value > 0.001
    assert arch_lm_test(x).p_value > 0.001
    assert jarque_bera_test(x).p_value > 0.001


def test_autocorrelation_is_detected() -> None:
    assert ljung_box_test(simulate_ar1(1000, seed=4, phi=0.3)).p_value < 1e-6


def test_volatility_clustering_is_detected() -> None:
    assert arch_lm_test(simulate_arch(3000, seed=4)).p_value < 1e-6


def test_fat_tails_are_detected() -> None:
    result = jarque_bera_test(np.random.default_rng(5).standard_t(3, 2000))

    assert result.p_value < 1e-6
    assert result.excess_kurtosis > 1.0


# ---- input validation ----


def test_diagnostics_reject_bad_input() -> None:
    with pytest.raises(ValueError, match="zero variance"):
        ljung_box_test(np.ones(100))
    with pytest.raises(ValueError, match="zero variance"):
        jarque_bera_test(np.ones(100))
    with pytest.raises(ValueError, match="constant"):
        arch_lm_test(np.ones(100))
    with pytest.raises(ValueError, match="at least"):
        ljung_box_test(np.arange(5.0), lags=10)
    with pytest.raises(ValueError, match="NaN"):
        jarque_bera_test(np.array([1.0, 2.0, float("nan")] * 5))
    with pytest.raises(ValueError, match="lags"):
        arch_lm_test(np.arange(50.0), lags=0)


# ---- badge rules ----


@pytest.mark.parametrize(
    ("r2", "z_market", "z_sector", "lb_p", "expected"),
    [
        (0.50, 0.5, -0.5, 0.40, "good"),
        (R2_CAUTION, 0.0, 0.0, 0.40, "good"),
        (0.20, 0.0, 0.0, 0.40, "caution"),
        (0.05, 0.0, 0.0, 0.40, "poor"),
        (0.50, 4.0, 0.0, 0.40, "caution"),
        (0.50, 0.0, -4.0, 0.40, "caution"),
        (0.50, 0.0, 0.0, 0.001, "caution"),
        (0.05, 4.0, 0.0, 0.001, "poor"),
    ],
)
def test_classify_health_rules(
    r2: float, z_market: float, z_sector: float, lb_p: float, expected: str
) -> None:
    status, _ = classify_health(r2, z_market, z_sector, lb_p)

    assert status == expected


def test_classify_health_explains_itself() -> None:
    good_status, good_reasons = classify_health(0.5, 0.0, 0.0, 0.5)
    _, poor_reasons = classify_health(0.05, 0.0, 0.0, 0.5)

    assert good_status == "good"
    assert good_reasons == ()
    assert any("R-squared" in reason for reason in poor_reasons)


# ---- end to end on synthetic stocks ----


def test_clean_data_is_healthy() -> None:
    stock, market, sector = make_panel(n=800).arrays()

    health = assess_model_health(stock, market, sector)

    assert health.status == "good"
    assert health.reasons == ()
    assert health.r_squared > 0.6
    assert abs(health.beta_z_market) < BETA_Z_CAUTION
    assert np.isfinite(health.jarque_bera.excess_kurtosis)


def test_changing_beta_is_flagged() -> None:
    n = 800
    market, pure, sector = factors(n, seed=11)
    noise = np.random.default_rng(11).normal(0.0, NOISE_STD, n)
    beta = np.where(np.arange(n) < n // 2, 0.5, 1.8)
    stock = beta * market + 0.7 * pure + noise

    health = assess_model_health(stock, market, sector)

    assert health.status == "caution"
    assert abs(health.beta_z_market) > BETA_Z_CAUTION
    assert any("changed" in reason for reason in health.reasons)


def test_autocorrelated_residuals_are_flagged() -> None:
    n = 800
    market, pure, sector = factors(n, seed=12)
    noise = 0.006 * simulate_ar1(n, seed=12, phi=0.5)
    stock = TRUE_BETA_MARKET * market + 0.7 * pure + noise

    health = assess_model_health(stock, market, sector)

    assert health.status == "caution"
    assert health.ljung_box.p_value < 0.01
    assert any("autocorrelated" in reason for reason in health.reasons)


def test_unrelated_stock_is_poor() -> None:
    _, market, sector = make_panel(n=800).arrays()
    stock = np.random.default_rng(13).normal(0.0, 0.01, 800)

    health = assess_model_health(stock, market, sector)

    assert health.status == "poor"
    assert health.r_squared < 0.10


def test_beta_stability_is_near_zero_for_stable_data() -> None:
    stock, market, sector = make_panel(n=1000).arrays()

    z_market, z_sector = beta_stability_z(stock, market, sector)

    assert abs(z_market) < BETA_Z_CAUTION
    assert abs(z_sector) < BETA_Z_CAUTION


def test_assess_rejects_bad_input() -> None:
    stock, market, sector = make_panel(n=300).arrays()

    with pytest.raises(ValueError, match="same length"):
        assess_model_health(stock[:-1], market, sector)
    with pytest.raises(ValueError, match="at least"):
        assess_model_health(stock[:100], market[:100], sector[:100])
    with pytest.raises(ValueError, match="zero variance"):
        assess_model_health(np.zeros(300), market, sector)
