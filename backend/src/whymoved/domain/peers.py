from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.cluster.hierarchy import leaves_list, linkage, optimal_leaf_ordering
from scipy.spatial.distance import squareform
from scipy.stats import norm

from whymoved.domain.factors import MIN_OBS, orthogonalize_sector

FloatArray = npt.NDArray[np.float64]

DEFAULT_LEVEL = 0.95
MIN_CORR_OBS = 30
CONSTANT_TOL = 1e-12


def _check_frame(frame: pd.DataFrame, minimum: int) -> FloatArray:
    if frame.shape[1] < 1:
        raise ValueError("no columns")
    if not frame.columns.is_unique:
        raise ValueError("column names must be unique")
    values = np.asarray(frame, dtype=np.float64)
    if len(values) < minimum:
        raise ValueError(f"need at least {minimum} observations, got {len(values)}")
    if not np.isfinite(values).all():
        raise ValueError("data contains NaN or infinite values")
    return values


def _finite(values: FloatArray, name: str) -> None:
    if not np.isfinite(values).all():
        raise ValueError(f"{name} contains NaN or infinite values")


def _check_aligned(
    returns: pd.DataFrame, market: pd.Series[float], sector_returns: pd.DataFrame
) -> None:
    if not returns.index.is_monotonic_increasing or not returns.index.is_unique:
        raise ValueError("the index must be sorted and unique")
    if not returns.index.equals(market.index):
        raise ValueError("market must have the same index as the returns")
    if not returns.index.equals(sector_returns.index):
        raise ValueError("sector_returns must have the same index as the returns")


def _columns_by_sector(sectors: tuple[str, ...]) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for i, sector in enumerate(sectors):
        groups.setdefault(sector, []).append(i)
    return groups


@dataclass(frozen=True, eq=False)
class Residualizer:
    """Each stock's exposure to the market and its pure sector factor, fitted on one window.

    Applying it to another window gives residuals that are out of sample: nothing about
    that window is used to estimate the betas or the sector orthogonalisation.
    """

    tickers: tuple[str, ...]
    sectors: tuple[str, ...]
    alpha: FloatArray
    beta_market: FloatArray
    beta_sector: FloatArray
    gamma: dict[str, tuple[float, float]]  # sector -> (intercept, slope) on the market

    def exposures(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "sector": list(self.sectors),
                "alpha": self.alpha,
                "beta_market": self.beta_market,
                "beta_sector": self.beta_sector,
            },
            index=pd.Index(list(self.tickers), name="ticker"),
        )


def fit_residualizer(
    returns: pd.DataFrame,
    market: pd.Series[float],
    sector_returns: pd.DataFrame,
    sector_of: Mapping[str, str],
) -> Residualizer:
    """Fit stock = alpha + b_m * market + b_s * pure_sector + residual for every stock.

    The pure sector factor is the sector return after removing its own market component,
    exactly as in the single-stock factor model. Stocks are fitted one sector at a time.
    """
    _check_aligned(returns, market, sector_returns)
    y = _check_frame(returns, MIN_OBS)
    m = np.asarray(market, dtype=np.float64)
    _finite(m, "market")
    _finite(np.asarray(sector_returns, dtype=np.float64), "sector_returns")

    tickers = tuple(str(c) for c in returns.columns)
    missing = [t for t in tickers if t not in sector_of]
    if missing:
        raise ValueError(f"no sector given for: {missing[:5]}")
    sectors = tuple(sector_of[t] for t in tickers)
    absent = sorted(set(sectors) - {str(c) for c in sector_returns.columns})
    if absent:
        raise ValueError(f"no sector return series for: {absent}")

    n_days, n_stocks = y.shape
    alpha = np.zeros(n_stocks, dtype=np.float64)
    beta_market = np.zeros(n_stocks, dtype=np.float64)
    beta_sector = np.zeros(n_stocks, dtype=np.float64)
    gamma: dict[str, tuple[float, float]] = {}

    for sector, cols in _columns_by_sector(sectors).items():
        sector_values = np.asarray(sector_returns[sector], dtype=np.float64)
        g0, g1, pure = orthogonalize_sector(sector_values, m)
        gamma[sector] = (g0, g1)
        design = np.column_stack([np.ones(n_days), m, pure])
        coef, *_ = np.linalg.lstsq(design, y[:, cols], rcond=None)
        alpha[cols] = coef[0]
        beta_market[cols] = coef[1]
        beta_sector[cols] = coef[2]

    return Residualizer(
        tickers=tickers,
        sectors=sectors,
        alpha=alpha,
        beta_market=beta_market,
        beta_sector=beta_sector,
        gamma=gamma,
    )


def residualize(
    model: Residualizer,
    returns: pd.DataFrame,
    market: pd.Series[float],
    sector_returns: pd.DataFrame,
) -> pd.DataFrame:
    """Residuals of every stock in the model, using the model's fixed coefficients."""
    _check_aligned(returns, market, sector_returns)
    missing = [t for t in model.tickers if t not in returns.columns]
    if missing:
        raise ValueError(f"returns are missing columns: {missing[:5]}")

    y = _check_frame(returns[list(model.tickers)], 2)
    m = np.asarray(market, dtype=np.float64)
    _finite(m, "market")

    out = np.empty_like(y)
    for sector, cols in _columns_by_sector(model.sectors).items():
        if sector not in sector_returns.columns:
            raise ValueError(f"no sector return series for: {sector}")
        sector_values = np.asarray(sector_returns[sector], dtype=np.float64)
        _finite(sector_values, "sector_returns")
        g0, g1 = model.gamma[sector]
        pure = sector_values - g0 - g1 * m
        out[:, cols] = (
            y[:, cols]
            - model.alpha[cols]
            - np.outer(m, model.beta_market[cols])
            - np.outer(pure, model.beta_sector[cols])
        )
    return pd.DataFrame(out, index=returns.index, columns=list(model.tickers))


def correlation_with(frame: pd.DataFrame, target: str) -> pd.Series[float]:
    """Pearson correlation of every column with `target`. Constant columns give NaN."""
    values = _check_frame(frame, MIN_CORR_OBS)
    names = [str(c) for c in frame.columns]
    if target not in names:
        raise KeyError(f"{target} is not a column")
    j = names.index(target)

    centered = values - values.mean(axis=0)
    norms = np.sqrt(np.sum(centered**2, axis=0))
    constant = norms <= CONSTANT_TOL * np.sqrt(len(values))
    if bool(constant[j]):
        raise ValueError("the target has zero variance")

    safe = np.where(constant, 1.0, norms)
    corr = np.clip((centered.T @ centered[:, j]) / (safe * safe[j]), -1.0, 1.0)
    corr[constant] = np.nan
    return pd.Series(corr, index=frame.columns)


def correlation_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Full Pearson correlation matrix. Rows and columns of constant series are NaN."""
    values = _check_frame(frame, MIN_CORR_OBS)
    centered = values - values.mean(axis=0)
    norms = np.sqrt(np.sum(centered**2, axis=0))
    constant = norms <= CONSTANT_TOL * np.sqrt(len(values))

    safe = np.where(constant, 1.0, norms)
    corr = np.clip((centered.T @ centered) / np.outer(safe, safe), -1.0, 1.0)
    corr[constant, :] = np.nan
    corr[:, constant] = np.nan
    ok = np.nonzero(~constant)[0]
    corr[ok, ok] = 1.0
    return pd.DataFrame(corr, index=frame.columns, columns=frame.columns)


def fisher_interval(
    r: FloatArray, n: int, level: float = DEFAULT_LEVEL
) -> tuple[FloatArray, FloatArray]:
    """Fisher z confidence interval for correlations estimated from n observations.

    Assumes independent, roughly normal observations. Fat tails and volatility clustering
    make it somewhat too narrow, so treat it as a guide, not a guarantee.
    """
    if n <= 3:
        raise ValueError("need more than 3 observations")
    if not 0.0 < level < 1.0:
        raise ValueError("level must be between 0 and 1")

    z = np.arctanh(np.clip(np.asarray(r, dtype=np.float64), -0.999999, 0.999999))
    half = float(norm.ppf(0.5 + level / 2.0)) / float(np.sqrt(n - 3))
    low = np.asarray(np.tanh(z - half), dtype=np.float64)
    high = np.asarray(np.tanh(z + half), dtype=np.float64)
    return low, high


def rank_peers(
    frame: pd.DataFrame, target: str, top_k: int, level: float = DEFAULT_LEVEL
) -> pd.DataFrame:
    """The `top_k` columns most correlated with `target`, highest first.

    Pass residuals for the default ranking or raw returns for the raw layer. Columns:
    corr, ci_low, ci_high. Stocks with undefined correlation are skipped.
    """
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    corr = correlation_with(frame, target).drop(index=target).dropna()
    ranked = corr.sort_values(ascending=False, kind="mergesort").head(top_k)

    values = np.asarray(ranked, dtype=np.float64)
    low, high = fisher_interval(values, len(frame), level)
    return pd.DataFrame(
        {"corr": values, "ci_low": low, "ci_high": high},
        index=pd.Index([str(t) for t in ranked.index], name="ticker"),
    )


def validate_peers(
    selection: pd.DataFrame,
    validation: pd.DataFrame,
    target: str,
    peers: Sequence[str],
    level: float = DEFAULT_LEVEL,
) -> pd.DataFrame:
    """Each peer's correlation with the target on the selection and the validation window.

    Residuals for both windows must come from the same Residualizer, fitted on the
    selection window. Columns: corr_selection, corr_validation, change, val_ci_low,
    val_ci_high.
    """
    names = list(peers)
    if not names:
        raise ValueError("peers must not be empty")
    if len(set(names)) != len(names) or target in names:
        raise ValueError("peers must be unique and exclude the target")

    selected = correlation_with(selection, target)
    held_out = correlation_with(validation, target)
    for ticker in names:
        if ticker not in selected.index or ticker not in held_out.index:
            raise KeyError(f"{ticker} is not a column of both windows")

    corr_selection = np.asarray(selected.loc[names], dtype=np.float64)
    corr_validation = np.asarray(held_out.loc[names], dtype=np.float64)
    low, high = fisher_interval(corr_validation, len(validation), level)
    return pd.DataFrame(
        {
            "corr_selection": corr_selection,
            "corr_validation": corr_validation,
            "change": corr_validation - corr_selection,
            "val_ci_low": low,
            "val_ci_high": high,
        },
        index=pd.Index(names, name="ticker"),
    )


def cluster_order(corr: pd.DataFrame) -> list[str]:
    """Order a correlation matrix's columns by average-linkage hierarchical clustering.

    Uses the distance sqrt((1 - r) / 2) and optimal leaf ordering, so related stocks end
    up next to each other in the grid.
    """
    names = [str(c) for c in corr.columns]
    n = len(names)
    if corr.shape != (n, n) or [str(i) for i in corr.index] != names:
        raise ValueError("corr must be a square matrix with matching rows and columns")
    values = np.asarray(corr, dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("corr contains NaN or infinite values")
    if not np.allclose(values, values.T, atol=1e-6):
        raise ValueError("corr must be symmetric")
    if n <= 2:
        return names

    dist = np.sqrt(0.5 * (1.0 - np.clip(values, -1.0, 1.0)))
    dist = (dist + dist.T) / 2.0
    np.fill_diagonal(dist, 0.0)
    condensed = squareform(dist, checks=False)
    tree = linkage(condensed, method="average")
    order = np.asarray(leaves_list(optimal_leaf_ordering(tree, condensed)), dtype=np.int64)
    return [names[int(i)] for i in order]
