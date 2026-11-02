from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from whymoved.domain.bars import DailyBars


def frame(columns: list[str], days: int = 3) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-02", periods=days)
    return pd.DataFrame(np.ones((days, len(columns))), index=index, columns=columns)


def test_symbols_follow_the_column_order() -> None:
    bars = DailyBars(close=frame(["B", "A"]), volume=frame(["B", "A"]))

    assert bars.symbols == ["B", "A"]


def test_mismatched_frames_are_rejected() -> None:
    with pytest.raises(ValueError, match="dates"):
        DailyBars(close=frame(["A"], days=3), volume=frame(["A"], days=4))
    with pytest.raises(ValueError, match="symbols"):
        DailyBars(close=frame(["A"]), volume=frame(["B"]))
