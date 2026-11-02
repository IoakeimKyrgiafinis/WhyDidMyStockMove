from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
import statsmodels.api as sm

FloatArray = npt.NDArray[np.float64]

DEFAULT_WINDOW = 252
DEFAULT_NW_LAGS = 5
MIN_OBS = 60


@dataclass(frozen=True, eq=False)
class FactorFit:
    """OLS fit of stock returns on market and orthogonalised sector returns."""

    alpha: float
    beta_market: float
    beta_sector: float
    cov: FloatArray  # Newey-West covariance of (alpha, beta_market, beta_sector)
    gamma0: float  # intercept of sector-on-market regression
    gamma1: float  # slope of sector-on-market regression
    resid_std: float
    r_squared: float
    n_obs: int

    @property
    def se_beta_market(self) -> float:
        return float(np.sqrt(self.cov[1, 1]))

    @property
    def se_beta_sector(self) -> float:
        return float(np.sqrt(self.cov[2, 2]))


@dataclass(frozen=True)
class DayExplanation:
    """One day's return split into baseline drift, market, sector and unexplained."""

    date: pd.Timestamp
    total: float
    alpha: float
    market: float
    sector: float
    unexplained: float
    se_market: float
    se_sector: float
    se_explained: float  # standard error of alpha + market + sector together
    fit: FactorFit


def _align(
    stock: pd.Series[float], market: pd.Series[float], sector: pd.Series[float]
) -> pd.DataFrame:
    df = pd.concat({"stock": stock, "market": market, "sector": sector}, axis=1).dropna()
    if not df.index.is_monotonic_increasing or not df.index.is_unique:
        raise ValueError("series must have a sorted, unique index")
    return df


def orthogonalize_sector(sector: FloatArray, market: FloatArray) -> tuple[float, float, FloatArray]:
    """Regress the sector on the market and return (intercept, slope, residual).

    The residual is the 'pure sector' factor: it carries no market component,
    so the market and sector contributions do not overlap.
    """
    x = np.column_stack([np.ones(len(market)), market])
    coef, *_ = np.linalg.lstsq(x, sector, rcond=None)
    gamma0, gamma1 = float(coef[0]), float(coef[1])
    pure: FloatArray = sector - gamma0 - gamma1 * market
    return gamma0, gamma1, pure


def fit_factor_model(
    stock: FloatArray,
    market: FloatArray,
    sector: FloatArray,
    nw_lags: int = DEFAULT_NW_LAGS,
) -> FactorFit:
    """Fit stock = alpha + b_m * market + b_s * pure_sector + e with Newey-West errors."""
    n = len(stock)
    if len(market) != n or len(sector) != n:
        raise ValueError("stock, market and sector must have the same length")
    if n < MIN_OBS:
        raise ValueError(f"need at least {MIN_OBS} observations, got {n}")
    if not (np.isfinite(stock).all() and np.isfinite(market).all() and np.isfinite(sector).all()):
        raise ValueError("inputs contain NaN or infinite values")

    gamma0, gamma1, pure = orthogonalize_sector(sector, market)
    x = np.column_stack([np.ones(n), market, pure])
    result = sm.OLS(stock, x).fit(cov_type="HAC", cov_kwds={"maxlags": nw_lags})

    params = np.asarray(result.params, dtype=np.float64)
    resid = np.asarray(result.resid, dtype=np.float64)
    return FactorFit(
        alpha=float(params[0]),
        beta_market=float(params[1]),
        beta_sector=float(params[2]),
        cov=np.asarray(result.cov_params(), dtype=np.float64),
        gamma0=gamma0,
        gamma1=gamma1,
        resid_std=float(np.sqrt(np.sum(resid**2) / (n - 3))),
        r_squared=float(result.rsquared),
        n_obs=n,
    )


def explain_day(
    stock: pd.Series[float],
    market: pd.Series[float],
    sector: pd.Series[float],
    date: pd.Timestamp,
    window: int = DEFAULT_WINDOW,
    nw_lags: int = DEFAULT_NW_LAGS,
) -> DayExplanation:
    """Explain one day's return using betas estimated only on the prior `window` days."""
    df = _align(stock, market, sector)
    pos = int(df.index.get_indexer(pd.Index([date]))[0])
    if pos == -1:
        raise KeyError(f"{date} is not in the data")
    if pos < window:
        raise ValueError(f"need {window} days of history before {date}, have {pos}")

    y = df["stock"].to_numpy(dtype=np.float64)
    m = df["market"].to_numpy(dtype=np.float64)
    s = df["sector"].to_numpy(dtype=np.float64)

    sl = slice(pos - window, pos)  # strictly before the day being explained
    fit = fit_factor_model(y[sl], m[sl], s[sl], nw_lags)

    pure_t = s[pos] - fit.gamma0 - fit.gamma1 * m[pos]
    market_part = fit.beta_market * m[pos]
    sector_part = fit.beta_sector * pure_t
    unexplained = y[pos] - fit.alpha - market_part - sector_part

    x = np.array([1.0, m[pos], pure_t])
    se_explained = float(np.sqrt(max(float(x @ fit.cov @ x), 0.0)))

    return DayExplanation(
        date=date,
        total=float(y[pos]),
        alpha=fit.alpha,
        market=float(market_part),
        sector=float(sector_part),
        unexplained=float(unexplained),
        se_market=float(abs(m[pos]) * fit.se_beta_market),
        se_sector=float(abs(pure_t) * fit.se_beta_sector),
        se_explained=se_explained,
        fit=fit,
    )


def rolling_decomposition(
    stock: pd.Series[float],
    market: pd.Series[float],
    sector: pd.Series[float],
    window: int = DEFAULT_WINDOW,
) -> pd.DataFrame:
    """Out-of-sample decomposition for every day after the first `window` days.

    Columns: total, market, sector, alpha, unexplained. Each day uses only the
    prior `window` days. Point estimates only (no standard errors), for speed.
    """
    df = _align(stock, market, sector)
    n = len(df)
    if n <= window:
        raise ValueError(f"need more than {window} observations, got {n}")

    y = df["stock"].to_numpy(dtype=np.float64)
    m = df["market"].to_numpy(dtype=np.float64)
    s = df["sector"].to_numpy(dtype=np.float64)

    out = np.empty((n - window, 5), dtype=np.float64)
    for i in range(window, n):
        sl = slice(i - window, i)
        gamma0, gamma1, pure = orthogonalize_sector(s[sl], m[sl])
        x = np.column_stack([np.ones(window), m[sl], pure])
        coef, *_ = np.linalg.lstsq(x, y[sl], rcond=None)

        pure_t = s[i] - gamma0 - gamma1 * m[i]
        market_part = coef[1] * m[i]
        sector_part = coef[2] * pure_t
        alpha_part = coef[0]
        out[i - window] = [
            y[i],
            market_part,
            sector_part,
            alpha_part,
            y[i] - alpha_part - market_part - sector_part,
        ]

    return pd.DataFrame(
        out,
        index=df.index[window:],
        columns=["total", "market", "sector", "alpha", "unexplained"],
    )
