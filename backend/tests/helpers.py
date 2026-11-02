from __future__ import annotations

from dataclasses import dataclass
from typing import SupportsFloat, SupportsInt, cast

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatArray = npt.NDArray[np.float64]


def as_float(value: object) -> float:
    """A pandas scalar as a float (pandas-stubs types .loc scalars as a wide union)."""
    return float(cast(SupportsFloat, value))


def as_int(value: object) -> int:
    return int(cast(SupportsInt, value))


def is_missing(value: object) -> bool:
    return bool(pd.isna(cast(float | None, value)))


TRUE_BETA_MARKET = 1.3
TRUE_BETA_SECTOR = 0.7
NOISE_STD = 0.008
MARKET_STD = 0.010
PURE_SECTOR_STD = 0.006


@dataclass(frozen=True)
class Panel:
    stock: pd.Series[float]
    market: pd.Series[float]
    sector: pd.Series[float]

    def arrays(self) -> tuple[FloatArray, FloatArray, FloatArray]:
        return (
            np.asarray(self.stock, dtype=np.float64),
            np.asarray(self.market, dtype=np.float64),
            np.asarray(self.sector, dtype=np.float64),
        )


def make_panel(n: int = 2000, seed: int = 7) -> Panel:
    """Synthetic returns with known betas: the stock loads 1.3 on the market
    and 0.7 on the pure sector factor, plus idiosyncratic noise."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2015-01-01", periods=n)
    market = rng.normal(0.0004, MARKET_STD, n)
    pure = rng.normal(0.0, PURE_SECTOR_STD, n)
    sector = 0.0001 + 0.8 * market + pure
    stock = 0.0002 + TRUE_BETA_MARKET * market + TRUE_BETA_SECTOR * pure
    stock = stock + rng.normal(0.0, NOISE_STD, n)
    return Panel(
        stock=pd.Series(stock, index=index, name="stock"),
        market=pd.Series(market, index=index, name="market"),
        sector=pd.Series(sector, index=index, name="sector"),
    )


def true_variance_shares() -> tuple[float, float, float]:
    """Population variance shares (market, sector, idiosyncratic) implied by make_panel."""
    market = TRUE_BETA_MARKET**2 * MARKET_STD**2
    sector = TRUE_BETA_SECTOR**2 * PURE_SECTOR_STD**2
    idio = NOISE_STD**2
    total = market + sector + idio
    return market / total, sector / total, idio / total
