from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pandas as pd
import pytest

from whymoved.domain.cointegration import cointegration_screen, engle_granger

FloatArray = npt.NDArray[np.float64]


def random_walk(n: int, rng: np.random.Generator) -> FloatArray:
    return np.asarray(np.log(100.0) + np.cumsum(rng.normal(0.0003, 0.012, n)), dtype=np.float64)


def ar1(n: int, rho: float, std: float, rng: np.random.Generator) -> FloatArray:
    shocks = rng.normal(0.0, std, n)
    out = np.zeros(n)
    for t in range(1, n):
        out[t] = rho * out[t - 1] + shocks[t]
    return out


def to_prices(log_prices: FloatArray, index: pd.DatetimeIndex) -> pd.Series[float]:
    return pd.Series(np.exp(log_prices), index=index)


def test_cointegrated_pair_is_detected_and_the_hedge_ratio_recovered() -> None:
    rng = np.random.default_rng(0)
    n = 600
    index = pd.bdate_range("2018-01-01", periods=n)
    x = random_walk(n, rng)
    y = 0.7 + 1.5 * x + ar1(n, 0.8, 0.02, rng)

    result = engle_granger(to_prices(y, index), to_prices(x, index))

    assert result.p_value < 0.01
    assert result.hedge_ratio == pytest.approx(1.5, abs=0.1)
    assert result.n_obs == n


def test_independent_random_walks_are_rarely_flagged() -> None:
    n, reps = 500, 80
    index = pd.bdate_range("2018-01-01", periods=n)
    flagged = 0
    for seed in range(reps):
        rng = np.random.default_rng(seed)
        a = to_prices(random_walk(n, rng), index)
        b = to_prices(random_walk(n, rng), index)
        if engle_granger(a, b).p_value < 0.05:
            flagged += 1

    assert flagged / reps < 0.15  # about 0.05 expected


def test_result_does_not_depend_on_the_price_scale() -> None:
    rng = np.random.default_rng(2)
    n = 400
    index = pd.bdate_range("2018-01-01", periods=n)
    x = random_walk(n, rng)
    y = 0.2 + 1.2 * x + ar1(n, 0.85, 0.02, rng)
    base = engle_granger(to_prices(y, index), to_prices(x, index))

    scaled = engle_granger(to_prices(y, index) * 7.0, to_prices(x, index))

    assert scaled.p_value == pytest.approx(base.p_value, rel=1e-6)
    assert scaled.hedge_ratio == pytest.approx(base.hedge_ratio, rel=1e-9)


def test_screen_flags_planted_pairs_and_controls_false_discoveries() -> None:
    rng = np.random.default_rng(3)
    n = 600
    index = pd.bdate_range("2018-01-01", periods=n)
    x = random_walk(n, rng)
    columns = {"T": to_prices(x, index)}
    real = ["R1", "R2", "R3"]
    for k, name in enumerate(real):
        spread = ar1(n, 0.8, 0.02, rng)
        columns[name] = to_prices(0.3 * k + (1.0 + 0.4 * k) * x + spread, index)
    fake = [f"F{k}" for k in range(7)]
    for name in fake:
        columns[name] = to_prices(random_walk(n, rng), index)
    prices = pd.DataFrame(columns)

    out = cointegration_screen(prices, "T", real + fake)

    assert out.loc[real, "cointegrated"].all()
    assert int(out.loc[fake, "cointegrated"].sum()) <= 1
    assert (out["q_value"] >= out["p_value"] - 1e-12).all()
    assert list(out.index) == real + fake


def test_invalid_inputs_are_rejected() -> None:
    n = 200
    index = pd.bdate_range("2018-01-01", periods=n)
    good = pd.Series(np.linspace(100.0, 120.0, n) + np.arange(n) % 3, index=index)
    other = pd.Series(np.linspace(50.0, 70.0, n) + np.arange(n) % 5, index=index)

    with pytest.raises(ValueError, match="same index"):
        engle_granger(good.iloc[1:], other)
    with pytest.raises(ValueError, match="at least"):
        engle_granger(good.iloc[:50], other.iloc[:50])
    with pytest.raises(ValueError, match="positive"):
        engle_granger(good - 200.0, other)
    with pytest.raises(ValueError, match="NaN"):
        engle_granger(good.where(good < 119.0), other)
    with pytest.raises(ValueError, match="constant"):
        engle_granger(pd.Series(100.0, index=index), other)


def test_screen_rejects_bad_requests() -> None:
    rng = np.random.default_rng(4)
    n = 200
    index = pd.bdate_range("2018-01-01", periods=n)
    prices = pd.DataFrame(
        {
            "T": to_prices(random_walk(n, rng), index),
            "A": to_prices(random_walk(n, rng), index),
        }
    )

    with pytest.raises(ValueError, match="empty"):
        cointegration_screen(prices, "T", [])
    with pytest.raises(ValueError, match="exclude the target"):
        cointegration_screen(prices, "T", ["T", "A"])
    with pytest.raises(KeyError):
        cointegration_screen(prices, "T", ["ZZZ"])
