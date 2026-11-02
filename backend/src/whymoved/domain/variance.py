from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from whymoved.domain.factors import MIN_OBS, orthogonalize_sector

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

DEFAULT_N_BOOT = 1000
DEFAULT_BLOCK = 10
DEFAULT_LEVEL = 0.95
MIN_BOOT = 100


@dataclass(frozen=True, eq=False)
class VarianceSplit:
    """Share of a stock's return variance due to the market, the pure sector, and itself.

    Arrays are ordered [market, sector, idiosyncratic].
    """

    shares: FloatArray
    ci_low: FloatArray
    ci_high: FloatArray
    n_obs: int
    n_boot: int
    block: int
    level: float

    @property
    def market(self) -> float:
        return float(self.shares[0])

    @property
    def sector(self) -> float:
        return float(self.shares[1])

    @property
    def idiosyncratic(self) -> float:
        return float(self.shares[2])


def variance_shares(stock: FloatArray, market: FloatArray, sector: FloatArray) -> FloatArray:
    """In-sample variance shares. Additive because the sector factor is orthogonalised."""
    n = len(stock)
    if len(market) != n or len(sector) != n:
        raise ValueError("stock, market and sector must have the same length")
    if n < MIN_OBS:
        raise ValueError(f"need at least {MIN_OBS} observations, got {n}")
    if not (np.isfinite(stock).all() and np.isfinite(market).all() and np.isfinite(sector).all()):
        raise ValueError("inputs contain NaN or infinite values")
    total = float(np.var(stock))
    if total <= 0.0:
        raise ValueError("stock returns have zero variance")

    _, _, pure = orthogonalize_sector(sector, market)
    x = np.column_stack([np.ones(n), market, pure])
    coef, *_ = np.linalg.lstsq(x, stock, rcond=None)
    resid = stock - x @ coef

    parts = [
        float(coef[1]) ** 2 * float(np.var(market)),
        float(coef[2]) ** 2 * float(np.var(pure)),
        float(np.var(resid)),
    ]
    shares = [part / total for part in parts]
    return np.asarray(shares, dtype=np.float64)


def block_bootstrap_indices(n: int, block: int, n_boot: int, rng: np.random.Generator) -> IntArray:
    """Moving-block bootstrap row indices, shape (n_boot, n).

    Each row is built from consecutive blocks of `block` observations, which keeps
    short-range dependence such as volatility clustering inside each resample.
    """
    if not 1 <= block <= n:
        raise ValueError("block must be between 1 and the number of observations")
    if n_boot < 1:
        raise ValueError("n_boot must be at least 1")

    n_blocks = -(-n // block)
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    offsets = np.arange(block)
    grid = starts[:, :, None] + offsets[None, None, :]
    idx = grid.reshape(n_boot, n_blocks * block)[:, :n]
    return np.asarray(idx, dtype=np.int64)


def variance_split(
    stock: FloatArray,
    market: FloatArray,
    sector: FloatArray,
    n_boot: int = DEFAULT_N_BOOT,
    block: int = DEFAULT_BLOCK,
    level: float = DEFAULT_LEVEL,
    seed: int = 0,
) -> VarianceSplit:
    """Variance shares with block-bootstrap percentile intervals."""
    if n_boot < MIN_BOOT:
        raise ValueError(f"n_boot must be at least {MIN_BOOT}")
    if not 0.0 < level < 1.0:
        raise ValueError("level must be between 0 and 1")

    point = variance_shares(stock, market, sector)
    n = len(stock)
    rng = np.random.default_rng(seed)
    idx = block_bootstrap_indices(n, block, n_boot, rng)

    boot = np.empty((n_boot, 3), dtype=np.float64)
    for b in range(n_boot):
        rows = idx[b]
        boot[b] = variance_shares(stock[rows], market[rows], sector[rows])

    tail = 100.0 * (1.0 - level) / 2.0
    low = np.percentile(boot, tail, axis=0)
    high = np.percentile(boot, 100.0 - tail, axis=0)
    return VarianceSplit(
        shares=point,
        ci_low=np.asarray(low, dtype=np.float64),
        ci_high=np.asarray(high, dtype=np.float64),
        n_obs=n,
        n_boot=n_boot,
        block=block,
        level=level,
    )
