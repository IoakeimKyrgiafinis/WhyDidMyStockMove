from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.stats import norm

FloatArray = npt.NDArray[np.float64]

DEFAULT_LAMBDA = 0.94
DEFAULT_WARMUP = 30
DEFAULT_LOOKBACK = 250
DEFAULT_MIN_HISTORY = 60
DEFAULT_ALPHA = 0.05


def ewma_volatility(
    residuals: FloatArray, lam: float = DEFAULT_LAMBDA, warmup: int = DEFAULT_WARMUP
) -> FloatArray:
    """Exponentially weighted volatility forecast for each day.

    sigma[t] uses only residuals[:t], never residuals[t], so a day's own move
    cannot dilute its own score. Values are NaN until `warmup` days of history.
    """
    if not 0.0 < lam < 1.0:
        raise ValueError("lam must be between 0 and 1")
    if warmup < 2:
        raise ValueError("warmup must be at least 2")
    if not np.isfinite(residuals).all():
        raise ValueError("residuals contain NaN or infinite values")

    n = len(residuals)
    sigma = np.full(n, np.nan, dtype=np.float64)
    if n <= warmup:
        return sigma

    variance = float(np.mean(residuals[:warmup] ** 2))
    sigma[warmup] = np.sqrt(variance)
    for t in range(warmup + 1, n):
        variance = lam * variance + (1.0 - lam) * float(residuals[t - 1] ** 2)
        sigma[t] = np.sqrt(variance)
    return sigma


def _standardize(residuals: FloatArray, sigma: FloatArray) -> FloatArray:
    scores = np.full(len(residuals), np.nan, dtype=np.float64)
    with np.errstate(invalid="ignore"):
        valid = sigma > 0.0
    scores[valid] = residuals[valid] / sigma[valid]
    return scores


def standardized_scores(
    residuals: FloatArray, lam: float = DEFAULT_LAMBDA, warmup: int = DEFAULT_WARMUP
) -> FloatArray:
    """Residual divided by its volatility forecast. NaN where sigma is unknown or zero."""
    return _standardize(residuals, ewma_volatility(residuals, lam, warmup))


def empirical_tail_probability(
    scores: FloatArray,
    lookback: int = DEFAULT_LOOKBACK,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> FloatArray:
    """Two-sided empirical tail probability of |score| against the previous `lookback` scores.

    p = (1 + #{past |s| >= |s_t|}) / (n_past + 1). The day itself counts once, so p is
    never zero. NaN until `min_history` finite past scores exist.
    """
    if min_history < 1 or lookback < min_history:
        raise ValueError("need 1 <= min_history <= lookback")

    n = len(scores)
    out = np.full(n, np.nan, dtype=np.float64)
    abs_scores = np.abs(scores)
    for t in range(n):
        if not np.isfinite(abs_scores[t]):
            continue
        history = abs_scores[max(0, t - lookback) : t]
        history = history[np.isfinite(history)]
        if len(history) < min_history:
            continue
        exceed = int(np.sum(history >= abs_scores[t]))
        out[t] = (1.0 + exceed) / (len(history) + 1.0)
    return out


def gaussian_two_sided_p(z: FloatArray) -> FloatArray:
    """Two-sided tail probability if returns were normally distributed."""
    return np.asarray(2.0 * norm.sf(np.abs(z)), dtype=np.float64)


def score_unexplained(
    unexplained: pd.Series[float],
    lam: float = DEFAULT_LAMBDA,
    warmup: int = DEFAULT_WARMUP,
    lookback: int = DEFAULT_LOOKBACK,
    min_history: int = DEFAULT_MIN_HISTORY,
    alpha: float = DEFAULT_ALPHA,
) -> pd.DataFrame:
    """Score every day's unexplained move.

    Columns: unexplained, sigma, z, p_value, p_normal, return_period, flagged.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be between 0 and 1")

    values = np.asarray(unexplained, dtype=np.float64)
    sigma = ewma_volatility(values, lam, warmup)
    z = _standardize(values, sigma)
    p_value = empirical_tail_probability(z, lookback, min_history)

    eligible = np.isfinite(p_value)
    flagged = np.zeros(len(values), dtype=np.bool_)
    flagged[eligible] = p_value[eligible] <= alpha

    return pd.DataFrame(
        {
            "unexplained": values,
            "sigma": sigma,
            "z": z,
            "p_value": p_value,
            "p_normal": gaussian_two_sided_p(z),
            "return_period": 1.0 / p_value,
            "flagged": flagged,
        },
        index=unexplained.index,
    )


def expected_false_alarms(scored: pd.DataFrame, alpha: float = DEFAULT_ALPHA) -> float:
    """Flags expected from chance alone: alpha times the number of scored days."""
    eligible = int(scored["p_value"].notna().sum())
    return alpha * eligible
