"""Synthetic 5-minute bars with a known intraday structure."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from whymoved.domain.timing import MARKET_TZ

BETA_MARKET = 1.1
BETA_SECTOR = 0.7
BARS_PER_DAY = 78  # 9:30 to 15:55


def sessions(n: int, last: date = date(2026, 10, 30)) -> list[date]:
    days = pd.bdate_range(end=pd.Timestamp(last), periods=n)
    return [d.date() for d in days]


def bar_times(day: date) -> list[datetime]:
    """Bar start times in naive UTC for one session (New York 9:30 to 15:55)."""
    local = [
        datetime.combine(day, time(9, 30)) + timedelta(minutes=5 * k) for k in range(BARS_PER_DAY)
    ]
    stamps = pd.DatetimeIndex(local).tz_localize(MARKET_TZ).tz_convert("UTC").tz_localize(None)
    return list(stamps.to_pydatetime())


def make_bars(
    n_days: int = 45,
    seed: int = 5,
    shocks: dict[tuple[date, int], float] | None = None,
    overnight_shocks: dict[date, float] | None = None,
    last: date = date(2026, 10, 30),
) -> tuple[pd.DataFrame, list[date]]:
    """Closes for stock, market and sector over `n_days` sessions.

    Residual volatility is U-shaped (twice as large in the first and last half hour), like
    real intraday data. `shocks` adds an abnormal return to (session, bar) of the stock;
    `overnight_shocks` adds one to a session's opening gap.
    """
    rng = np.random.default_rng(seed)
    shocks = shocks or {}
    overnight_shocks = overnight_shocks or {}
    days = sessions(n_days, last)
    level = {"stock": 50.0, "market": 500.0, "sector": 100.0}
    rows: list[dict[str, float]] = []
    index: list[datetime] = []
    u_shape = np.array([2.0 if k < 6 or k >= 72 else 1.0 for k in range(BARS_PER_DAY)])
    for day in days:
        gap_m = rng.normal(0.0, 0.004)
        gap_pure = rng.normal(0.0, 0.002)
        gap_stock = BETA_MARKET * gap_m + BETA_SECTOR * gap_pure + rng.normal(0.0, 0.004)
        gap_stock += overnight_shocks.get(day, 0.0)
        level["market"] *= 1 + gap_m
        level["sector"] *= 1 + 0.9 * gap_m + gap_pure
        level["stock"] *= 1 + gap_stock
        for k, when in enumerate(bar_times(day)):
            if k > 0:
                m = rng.normal(0.0, 0.0012)
                pure = rng.normal(0.0, 0.0008)
                noise = rng.normal(0.0, 0.0010 * u_shape[k])
                r = BETA_MARKET * m + BETA_SECTOR * pure + noise + shocks.get((day, k), 0.0)
                level["market"] *= 1 + m
                level["sector"] *= 1 + 0.9 * m + pure
                level["stock"] *= 1 + r
            rows.append(dict(level))
            index.append(when)
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index)), days


def at(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute))


def slot(hour: int, minute: int) -> int:
    return ((hour - 9) * 60 + minute - 30) // 5
