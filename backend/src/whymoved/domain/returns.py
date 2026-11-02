from __future__ import annotations

import pandas as pd


def simple_returns(prices: pd.Series[float]) -> pd.Series[float]:
    """Daily simple returns from a price series.

    Raises ValueError on NaNs or non-positive prices instead of silently
    returning wrong numbers.
    """
    if bool(prices.isna().any()):
        raise ValueError("prices contain NaN values")
    if bool((prices <= 0).any()):
        raise ValueError("prices must be strictly positive")
    return prices.pct_change().dropna()
