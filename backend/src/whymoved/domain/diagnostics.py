from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.stats import chi2

from whymoved.domain.factors import DEFAULT_NW_LAGS, MIN_OBS, fit_factor_model

FloatArray = npt.NDArray[np.float64]
Status = Literal["good", "caution", "poor"]

# Display heuristics, not statistical tests. To be recalibrated on real data in M2.
R2_CAUTION = 0.25
R2_POOR = 0.10
BETA_Z_CAUTION = 3.0
LJUNG_BOX_ALPHA = 0.01
LJUNG_BOX_LAGS = 10
ARCH_LAGS = 5

_RANK: dict[Status, int] = {"good": 0, "caution": 1, "poor": 2}


@dataclass(frozen=True)
class HypothesisTest:
    statistic: float
    p_value: float


@dataclass(frozen=True)
class JarqueBera:
    statistic: float
    p_value: float
    skewness: float
    excess_kurtosis: float


@dataclass(frozen=True)
class ModelHealth:
    status: Status
    reasons: tuple[str, ...]
    r_squared: float
    beta_z_market: float
    beta_z_sector: float
    jarque_bera: JarqueBera
    ljung_box: HypothesisTest
    arch_lm: HypothesisTest
    n_obs: int


def _check_series(x: FloatArray, minimum: int) -> FloatArray:
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError("expected a one-dimensional series")
    if len(arr) < minimum:
        raise ValueError(f"need at least {minimum} observations, got {len(arr)}")
    if not np.isfinite(arr).all():
        raise ValueError("series contains NaN or infinite values")
    return arr


def jarque_bera_test(x: FloatArray) -> JarqueBera:
    """Jarque-Bera normality test from sample skewness and kurtosis."""
    arr = _check_series(x, 8)
    centered = arr - float(arr.mean())
    m2 = float(np.mean(centered**2))
    if m2 <= 0.0:
        raise ValueError("series has zero variance")
    skew = float(np.mean(centered**3)) / m2**1.5
    kurt = float(np.mean(centered**4)) / m2**2
    stat = len(arr) / 6.0 * (skew**2 + (kurt - 3.0) ** 2 / 4.0)
    return JarqueBera(
        statistic=stat,
        p_value=float(chi2.sf(stat, 2)),
        skewness=skew,
        excess_kurtosis=kurt - 3.0,
    )


def ljung_box_test(x: FloatArray, lags: int = LJUNG_BOX_LAGS) -> HypothesisTest:
    """Ljung-Box test for autocorrelation up to `lags`."""
    if lags < 1:
        raise ValueError("lags must be at least 1")
    arr = _check_series(x, lags + 2)
    n = len(arr)
    centered = arr - float(arr.mean())
    denom = float(np.sum(centered**2))
    if denom <= 0.0:
        raise ValueError("series has zero variance")

    q = 0.0
    for k in range(1, lags + 1):
        rho = float(np.sum(centered[k:] * centered[:-k])) / denom
        q += rho**2 / (n - k)
    q *= n * (n + 2)
    return HypothesisTest(statistic=q, p_value=float(chi2.sf(q, lags)))


def arch_lm_test(x: FloatArray, lags: int = ARCH_LAGS) -> HypothesisTest:
    """Engle's ARCH-LM test for volatility clustering. Assumes mean-zero residuals."""
    if lags < 1:
        raise ValueError("lags must be at least 1")
    arr = _check_series(x, 2 * lags + 2)
    squared = arr**2
    n = len(squared)

    target = squared[lags:]
    columns = [squared[lags - k : n - k] for k in range(1, lags + 1)]
    design = np.column_stack([np.ones(n - lags), *columns])
    coef, *_ = np.linalg.lstsq(design, target, rcond=None)
    fitted = design @ coef

    ss_total = float(np.sum((target - target.mean()) ** 2))
    if ss_total <= 0.0:
        raise ValueError("squared series is constant")
    r_squared = 1.0 - float(np.sum((target - fitted) ** 2)) / ss_total
    stat = (n - lags) * r_squared
    return HypothesisTest(statistic=stat, p_value=float(chi2.sf(stat, lags)))


def _z(diff: float, se_a: float, se_b: float) -> float:
    denom = math.sqrt(se_a**2 + se_b**2)
    if denom == 0.0:
        return 0.0 if diff == 0.0 else math.copysign(math.inf, diff)
    return diff / denom


def beta_stability_z(
    stock: FloatArray,
    market: FloatArray,
    sector: FloatArray,
    nw_lags: int = DEFAULT_NW_LAGS,
) -> tuple[float, float]:
    """z-statistics for the change in (market, sector) beta between the two halves."""
    n = len(stock)
    if n < 2 * MIN_OBS:
        raise ValueError(f"need at least {2 * MIN_OBS} observations, got {n}")
    half = n // 2
    first = fit_factor_model(stock[:half], market[:half], sector[:half], nw_lags)
    second = fit_factor_model(stock[half:], market[half:], sector[half:], nw_lags)
    return (
        _z(first.beta_market - second.beta_market, first.se_beta_market, second.se_beta_market),
        _z(first.beta_sector - second.beta_sector, first.se_beta_sector, second.se_beta_sector),
    )


def _worse(a: Status, b: Status) -> Status:
    return a if _RANK[a] >= _RANK[b] else b


def classify_health(
    r_squared: float,
    beta_z_market: float,
    beta_z_sector: float,
    ljung_box_p: float,
) -> tuple[Status, tuple[str, ...]]:
    """Badge rules. Normality and ARCH results are deliberately not inputs."""
    status: Status = "good"
    reasons: list[str] = []

    if r_squared < R2_POOR:
        status = "poor"
        reasons.append(
            f"R-squared is {r_squared:.2f}: market and sector explain very little of this stock"
        )
    elif r_squared < R2_CAUTION:
        status = "caution"
        reasons.append(
            f"R-squared is {r_squared:.2f}: market and sector explain only part of this stock"
        )

    if max(abs(beta_z_market), abs(beta_z_sector)) > BETA_Z_CAUTION:
        status = _worse(status, "caution")
        reasons.append("market or sector exposure changed between the two halves of the window")

    if ljung_box_p < LJUNG_BOX_ALPHA:
        status = _worse(status, "caution")
        reasons.append("residuals are autocorrelated, so the model may be missing structure")

    return status, tuple(reasons)


def assess_model_health(
    stock: FloatArray,
    market: FloatArray,
    sector: FloatArray,
    nw_lags: int = DEFAULT_NW_LAGS,
) -> ModelHealth:
    """Fit the factor model on the whole window and test how well it describes the stock."""
    n = len(stock)
    if len(market) != n or len(sector) != n:
        raise ValueError("stock, market and sector must have the same length")
    if n < 2 * MIN_OBS:
        raise ValueError(f"need at least {2 * MIN_OBS} observations, got {n}")
    if float(np.var(stock)) <= 0.0:
        raise ValueError("stock returns have zero variance")

    fit = fit_factor_model(stock, market, sector, nw_lags)
    pure = sector - fit.gamma0 - fit.gamma1 * market
    resid = stock - fit.alpha - fit.beta_market * market - fit.beta_sector * pure

    z_market, z_sector = beta_stability_z(stock, market, sector, nw_lags)
    ljung_box = ljung_box_test(resid)
    status, reasons = classify_health(fit.r_squared, z_market, z_sector, ljung_box.p_value)

    return ModelHealth(
        status=status,
        reasons=reasons,
        r_squared=fit.r_squared,
        beta_z_market=z_market,
        beta_z_sector=z_sector,
        jarque_bera=jarque_bera_test(resid),
        ljung_box=ljung_box,
        arch_lm=arch_lm_test(resid),
        n_obs=n,
    )
