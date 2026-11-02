"""A synthetic universe with planted peer structure."""

from __future__ import annotations

import numpy as np
import pandas as pd

from whymoved.adapters.snapshot import Snapshot
from whymoved.domain.universe import MARKET_ETF, SECTOR_ETF_TICKERS, UniverseMember

LINKED = ("P1", "P2", "P3", "P4", "P5")  # share a hidden factor with AAA, in other sectors
COINTEGRATED = "COI"  # log price = log(AAA) + stationary noise
LINK_LOADING = 0.8
LINK_STD = 0.006
NOISE_STD = 0.008


def make_peer_snapshot(n_days: int = 900, n_other: int = 50, seed: int = 21) -> Snapshot:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2023-01-02", periods=n_days)
    market = rng.normal(0.0004, 0.01, n_days)
    returns: dict[str, np.ndarray] = {MARKET_ETF: market}
    pure: dict[str, np.ndarray] = {}
    for etf in SECTOR_ETF_TICKERS:
        pure[etf] = rng.normal(0.0, 0.005, n_days)
        returns[etf] = 0.9 * market + pure[etf]

    link = rng.normal(0.0, LINK_STD, n_days)
    members: list[UniverseMember] = []

    def add(ticker: str, etf: str, extra: np.ndarray) -> None:
        returns[ticker] = (
            1.1 * market + 0.6 * pure[etf] + extra + rng.normal(0.0, NOISE_STD, n_days)
        )
        members.append(UniverseMember(ticker, f"{ticker} INC", "Sector", etf, 1.0))

    add("AAA", "XLK", LINK_LOADING * link)
    for k, ticker in enumerate(LINKED):
        add(ticker, SECTOR_ETF_TICKERS[(k + 1) % len(SECTOR_ETF_TICKERS)], LINK_LOADING * link)
    for k in range(n_other):
        add(f"S{k:02d}", SECTOR_ETF_TICKERS[k % len(SECTOR_ETF_TICKERS)], np.zeros(n_days))

    close = pd.DataFrame({s: 100.0 * np.cumprod(1.0 + r) for s, r in returns.items()}, index=index)

    # COI tracks AAA's price level with mean-reverting noise, so the two are cointegrated.
    gap = np.zeros(n_days)
    shocks = rng.normal(0.0, 0.01, n_days)
    for t in range(1, n_days):
        gap[t] = 0.85 * gap[t - 1] + shocks[t]
    close[COINTEGRATED] = close["AAA"] * 0.5 * np.exp(gap)
    members.append(UniverseMember(COINTEGRATED, "COI INC", "Sector", "XLF", 1.0))

    # A late listing: incomplete history, so it is left out of the peer universe.
    late = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.01, n_days))
    late[: n_days // 2] = np.nan
    close["LATE"] = late
    members.append(UniverseMember("LATE", "LATE INC", "Sector", "XLE", 1.0))

    return Snapshot(
        close=close, members=members, as_of=index[-1].date(), history_start=index[0].date()
    )
