from __future__ import annotations

import pandas as pd
import pytest

from whymoved.domain.returns import simple_returns


def test_simple_returns_values() -> None:
    prices = pd.Series([100.0, 110.0, 99.0])

    result = simple_returns(prices)

    assert result.tolist() == pytest.approx([0.10, -0.10])


def test_simple_returns_rejects_nan() -> None:
    with pytest.raises(ValueError, match="NaN"):
        simple_returns(pd.Series([100.0, float("nan"), 101.0]))


def test_simple_returns_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        simple_returns(pd.Series([100.0, 0.0, 101.0]))
