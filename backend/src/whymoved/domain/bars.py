from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, eq=False)
class DailyBars:
    """Daily closes and volumes for a set of symbols, as wide frames.

    Both frames have one row per trading date (a timezone-naive DatetimeIndex) and one
    column per symbol, with NaN where a symbol has no bar. Symbols that were requested but
    returned nothing stay as all-NaN columns, so later quality checks report them instead
    of losing them.
    """

    close: pd.DataFrame
    volume: pd.DataFrame

    def __post_init__(self) -> None:
        if not self.close.index.equals(self.volume.index):
            raise ValueError("close and volume must have the same dates")
        if not self.close.columns.equals(self.volume.columns):
            raise ValueError("close and volume must have the same symbols")

    @property
    def symbols(self) -> list[str]:
        return [str(c) for c in self.close.columns]
