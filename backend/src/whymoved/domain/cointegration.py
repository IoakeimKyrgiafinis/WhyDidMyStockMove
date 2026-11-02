from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
from statsmodels.tsa.stattools import coint

from whymoved.domain.multiple_testing import benjamini_hochberg

FloatArray = npt.NDArray[np.float64]

MIN_COINT_OBS = 100
DEFAULT_Q = 0.05


@dataclass(frozen=True)
class CointegrationResult:
    statistic: float
    p_value: float
    hedge_ratio: float
    n_obs: int


def _log_prices(series: pd.Series[float], name: str) -> FloatArray:
    values = series.to_numpy(dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError(f"{name} contains NaN or infinite values")
    if bool((values <= 0.0).any()):
        raise ValueError(f"{name} must be strictly positive")
    return np.log(values)


def engle_granger(y: pd.Series[float], x: pd.Series[float]) -> CointegrationResult:
    """Engle-Granger cointegration test on log prices, with `y` as the dependent series.

    The p-value uses MacKinnon critical values for estimated residuals (statsmodels
    `coint`), which a plain ADF test on regression residuals does not. The hedge ratio is
    the OLS slope of log y on log x. The result depends on which series is dependent, so
    callers should use one fixed direction.
    """
    if not y.index.equals(x.index):
        raise ValueError("y and x must have the same index")
    if len(y) < MIN_COINT_OBS:
        raise ValueError(f"need at least {MIN_COINT_OBS} observations, got {len(y)}")

    log_y = _log_prices(y, "y")
    log_x = _log_prices(x, "x")
    if float(np.ptp(log_x)) == 0.0 or float(np.ptp(log_y)) == 0.0:
        raise ValueError("a price series is constant")

    result = coint(log_y, log_x)
    centered_x = log_x - log_x.mean()
    slope = float(np.sum(centered_x * (log_y - log_y.mean())) / np.sum(centered_x**2))
    return CointegrationResult(
        statistic=float(result[0]),
        p_value=float(result[1]),
        hedge_ratio=slope,
        n_obs=len(log_y),
    )


def cointegration_screen(
    prices: pd.DataFrame,
    target: str,
    peers: Sequence[str],
    q: float = DEFAULT_Q,
) -> pd.DataFrame:
    """Test the target against each peer and control false discoveries across the peers.

    Pass prices from the held-out window, not the window the peers were selected on.
    Columns: statistic, p_value, q_value (Benjamini-Hochberg adjusted), cointegrated,
    hedge_ratio. The correction covers the tests run here, not the screening that chose
    the peers; that is handled by selecting on an earlier window than the one tested.
    """
    names = list(peers)
    if not names:
        raise ValueError("peers must not be empty")
    if len(set(names)) != len(names) or target in names:
        raise ValueError("peers must be unique and exclude the target")
    for ticker in [target, *names]:
        if ticker not in prices.columns:
            raise KeyError(f"{ticker} is not a column of prices")

    results = [engle_granger(prices[target], prices[peer]) for peer in names]
    p_values = np.array([r.p_value for r in results], dtype=np.float64)
    q_values, rejected = benjamini_hochberg(p_values, q)

    return pd.DataFrame(
        {
            "statistic": [r.statistic for r in results],
            "p_value": p_values,
            "q_value": q_values,
            "cointegrated": rejected,
            "hedge_ratio": [r.hedge_ratio for r in results],
        },
        index=pd.Index(names, name="ticker"),
    )
