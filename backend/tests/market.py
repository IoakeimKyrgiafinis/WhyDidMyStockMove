"""A synthetic market with known structure, written to disk like the nightly job's snapshot."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from whymoved.adapters.parquet_store import ParquetPriceStore
from whymoved.adapters.snapshot import Snapshot
from whymoved.domain.bars import DailyBars
from whymoved.domain.universe import MARKET_ETF, SECTOR_ETF_TICKERS, UniverseMember

BETA_MARKET = 1.2
BETA_SECTOR = 0.6
SHOCK_DAY = 500
SHOCK = -0.08

MEMBERS = [
    UniverseMember("AAA", "ALPHA CORP", "Information Technology", "XLK", 5.0),
    UniverseMember("BBB", "BETA BANK", "Financials", "XLF", 3.0),
    UniverseMember("ABC", "ABC HOLDINGS", "Industrials", "XLI", 2.0),
    UniverseMember("NEW", "NEW LISTING", "Energy", "XLE", 1.0),
]


@dataclass(frozen=True, eq=False)
class Market:
    close: pd.DataFrame
    snapshot: Snapshot


def make_market(n_days: int = 700, seed: int = 11) -> Market:
    """Every stock loads BETA_MARKET on the market and BETA_SECTOR on its pure sector factor.

    AAA gets a SHOCK on day SHOCK_DAY. NEW only listed 100 days ago, too short to explain.
    """
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2023-01-02", periods=n_days)
    market = rng.normal(0.0004, 0.01, n_days)
    returns: dict[str, np.ndarray] = {MARKET_ETF: market}
    pure: dict[str, np.ndarray] = {}
    for etf in SECTOR_ETF_TICKERS:
        pure[etf] = rng.normal(0.0, 0.006, n_days)
        returns[etf] = 0.9 * market + pure[etf]
    for member in MEMBERS:
        noise = rng.normal(0.0, 0.008, n_days)
        returns[member.ticker] = (
            BETA_MARKET * market + BETA_SECTOR * pure[member.sector_etf] + noise
        )
    returns["AAA"][SHOCK_DAY] += SHOCK

    close = pd.DataFrame({s: 100.0 * np.cumprod(1.0 + r) for s, r in returns.items()}, index=index)
    close.loc[close.index[: n_days - 100], "NEW"] = np.nan
    snapshot = Snapshot(
        close=close,
        members=list(MEMBERS),
        as_of=index[-1].date(),
        history_start=index[0].date(),
    )
    return Market(close=close, snapshot=snapshot)


def write_snapshot(directory: Path, market: Market) -> None:
    """Write the files the nightly job publishes."""
    volume = pd.DataFrame(1_000_000.0, index=market.close.index, columns=market.close.columns)
    ParquetPriceStore(directory).save(DailyBars(close=market.close, volume=volume))
    summary = {
        "as_of": market.snapshot.as_of.isoformat(),
        "history_start": market.snapshot.history_start.isoformat(),
        "members": [
            {
                "ticker": m.ticker,
                "name": m.name,
                "sector": m.sector,
                "sector_etf": m.sector_etf,
                "weight": m.weight,
            }
            for m in MEMBERS
        ],
    }
    (directory / "universe.json").write_text(json.dumps(summary), encoding="utf-8")


def shock_date(market: Market) -> date:
    return pd.Timestamp(market.close.index[SHOCK_DAY]).date()
