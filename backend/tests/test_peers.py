from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from helpers import as_float
from whymoved.domain.peers import (
    cluster_order,
    correlation_matrix,
    correlation_with,
    fisher_interval,
    fit_residualizer,
    rank_peers,
    residualize,
    validate_peers,
)

SECTORS = ("A", "B", "C")
TRUE_PEERS = frozenset({"P1", "P2", "P3", "P4"})


@dataclass(frozen=True, eq=False)
class Universe:
    returns: pd.DataFrame
    market: pd.Series[float]
    sector_returns: pd.DataFrame
    sector_of: dict[str, str]
    target: str


def make_universe(n_days: int = 1500, seed: int = 21) -> Universe:
    """35 stocks in 3 sectors. Every stock loads 1 on the market and 1 on its sector.

    The target T and four peers share one extra factor. P1 and P2 are in T's sector A;
    P3 and P4 are in sector B. Ten other stocks per sector are not peers. Same-sector
    non-peers correlate more with T on raw returns than the cross-sector peers do, so only
    the residual ranking can find P3 and P4.
    """
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2015-01-01", periods=n_days)
    market = rng.normal(0.0004, 0.010, n_days)
    pure = {s: rng.normal(0.0, 0.012, n_days) for s in SECTORS}
    shared = rng.normal(0.0, 0.010, n_days)
    sector_returns = pd.DataFrame(
        {s: 0.0001 + 0.9 * market + pure[s] for s in SECTORS}, index=index
    )

    layout = {"T": "A", "P1": "A", "P2": "A", "P3": "B", "P4": "B"}
    for sector in SECTORS:
        for k in range(10):
            layout[f"{sector}{k:02d}"] = sector
    sharing = {"T", *TRUE_PEERS}

    columns: dict[str, np.ndarray] = {}
    for ticker, sector in layout.items():
        series = market + pure[sector] + rng.normal(0.0, 0.010, n_days)
        if ticker in sharing:
            series = series + shared
        columns[ticker] = series

    return Universe(
        returns=pd.DataFrame(columns, index=index),
        market=pd.Series(market, index=index),
        sector_returns=sector_returns,
        sector_of=layout,
        target="T",
    )


def make_noise_universe(
    n_stocks: int = 300, n_days: int = 500, seed: int = 5
) -> tuple[pd.DataFrame, pd.Series[float], pd.DataFrame, dict[str, str]]:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2018-01-01", periods=n_days)
    tickers = [f"S{k:03d}" for k in range(n_stocks)]
    returns = pd.DataFrame(rng.normal(0.0, 0.01, (n_days, n_stocks)), index=index, columns=tickers)
    market = pd.Series(rng.normal(0.0, 0.01, n_days), index=index)
    sectors = pd.DataFrame({"X": rng.normal(0.0, 0.01, n_days)}, index=index)
    return returns, market, sectors, dict.fromkeys(tickers, "X")


# ---- residualizing and ranking ----


def test_residual_ranking_finds_the_planted_peers_but_raw_ranking_does_not() -> None:
    u = make_universe()
    model = fit_residualizer(u.returns, u.market, u.sector_returns, u.sector_of)
    resid = residualize(model, u.returns, u.market, u.sector_returns)

    residual_top = set(rank_peers(resid, u.target, 4).index)
    raw_top = set(rank_peers(u.returns, u.target, 4).index)

    assert residual_top == set(TRUE_PEERS)
    assert not raw_top >= TRUE_PEERS
    assert "P3" not in raw_top


def test_residualizer_recovers_planted_exposures() -> None:
    u = make_universe()

    exposures = fit_residualizer(u.returns, u.market, u.sector_returns, u.sector_of).exposures()

    assert float((exposures["beta_market"] - 1.0).abs().max()) < 0.15
    assert float((exposures["beta_sector"] - 1.0).abs().max()) < 0.15


def test_in_sample_residuals_are_orthogonal_to_the_market() -> None:
    u = make_universe()
    model = fit_residualizer(u.returns, u.market, u.sector_returns, u.sector_of)

    resid = residualize(model, u.returns, u.market, u.sector_returns)

    assert float(resid.corrwith(u.market).abs().max()) < 1e-8
    assert float(resid.mean().abs().max()) < 1e-12


def test_out_of_sample_residuals_use_only_the_fitted_coefficients() -> None:
    u = make_universe()
    model = fit_residualizer(
        u.returns.iloc[:1000],
        u.market.iloc[:1000],
        u.sector_returns.iloc[:1000],
        u.sector_of,
    )
    out = residualize(
        model, u.returns.iloc[1000:], u.market.iloc[1000:], u.sector_returns.iloc[1000:]
    )

    row = model.exposures().loc["P1"]
    g0, g1 = model.gamma["A"]
    m = u.market.iloc[1000:]
    pure = u.sector_returns["A"].iloc[1000:] - g0 - g1 * m
    manual = (
        u.returns["P1"].iloc[1000:]
        - as_float(row["alpha"])
        - as_float(row["beta_market"]) * m
        - as_float(row["beta_sector"]) * pure
    )

    assert np.allclose(out["P1"].to_numpy(), manual.to_numpy(), atol=1e-12)


# ---- held-out validation ----


def test_planted_peers_survive_out_of_sample_validation() -> None:
    u = make_universe()
    first, last = slice(0, 1000), slice(1000, 1500)
    model = fit_residualizer(
        u.returns.iloc[first], u.market.iloc[first], u.sector_returns.iloc[first], u.sector_of
    )
    r_sel = residualize(
        model, u.returns.iloc[first], u.market.iloc[first], u.sector_returns.iloc[first]
    )
    r_val = residualize(
        model, u.returns.iloc[last], u.market.iloc[last], u.sector_returns.iloc[last]
    )

    peers = list(rank_peers(r_sel, u.target, 4).index)
    check = validate_peers(r_sel, r_val, u.target, peers)

    assert set(peers) == set(TRUE_PEERS)
    assert float(check["corr_validation"].min()) > 0.3
    assert abs(float(check["change"].mean())) < 0.1
    assert (check["val_ci_low"] <= check["corr_validation"]).all()
    assert (check["corr_validation"] <= check["val_ci_high"]).all()


def test_selecting_from_noise_shrinks_out_of_sample() -> None:
    returns, market, sectors, sector_of = make_noise_universe()
    first, last = slice(0, 250), slice(250, 500)
    model = fit_residualizer(
        returns.iloc[first], market.iloc[first], sectors.iloc[first], sector_of
    )
    r_sel = residualize(model, returns.iloc[first], market.iloc[first], sectors.iloc[first])
    r_val = residualize(model, returns.iloc[last], market.iloc[last], sectors.iloc[last])

    top = rank_peers(r_sel, "S000", 10)
    check = validate_peers(r_sel, r_val, "S000", list(top.index))

    assert float(check["corr_selection"].mean()) > 0.09
    assert abs(float(check["corr_validation"].mean())) < 0.08
    assert float(check["change"].mean()) < -0.07


# ---- correlation helpers ----


def test_correlation_with_matches_pandas() -> None:
    u = make_universe(n_days=400)

    mine = correlation_with(u.returns, "T")
    reference = u.returns.corr()["T"]

    assert np.allclose(mine.to_numpy(), reference.to_numpy(), atol=1e-12)
    assert float(mine["T"]) == pytest.approx(1.0)


def test_correlation_matrix_is_symmetric_with_a_unit_diagonal() -> None:
    u = make_universe(n_days=400)

    matrix = correlation_matrix(u.returns)
    values = matrix.to_numpy()

    assert np.allclose(values, values.T)
    assert np.allclose(np.diag(values), 1.0)
    assert np.allclose(values, np.corrcoef(u.returns.to_numpy(), rowvar=False), atol=1e-12)


def test_constant_column_gives_nan_and_is_skipped_in_ranking() -> None:
    u = make_universe(n_days=300)
    frame = u.returns[["T", "P1", "P2"]].copy()
    frame["K"] = 0.001

    corr = correlation_with(frame, "T")
    ranked = rank_peers(frame, "T", 5)

    assert np.isnan(float(corr["K"]))
    assert "K" not in ranked.index
    assert "T" not in ranked.index
    with pytest.raises(ValueError, match="zero variance"):
        correlation_with(frame, "K")


def test_fisher_interval_properties() -> None:
    low, high = fisher_interval(np.array([0.5]), 103)
    wide_low, wide_high = fisher_interval(np.array([0.5]), 30)
    narrow_low, narrow_high = fisher_interval(np.array([0.5]), 3000)

    assert float(low[0]) == pytest.approx(0.3393, abs=2e-3)
    assert float(high[0]) == pytest.approx(0.6323, abs=2e-3)
    assert float(wide_low[0]) < float(low[0]) < float(narrow_low[0])
    assert float(narrow_high[0]) < float(high[0]) < float(wide_high[0])
    assert np.isnan(fisher_interval(np.array([np.nan]), 100)[0][0])


def test_cluster_order_keeps_blocks_together() -> None:
    rng = np.random.default_rng(3)
    n = 1500
    f1, f2 = rng.normal(size=n), rng.normal(size=n)
    names: list[str] = []
    columns: dict[str, np.ndarray] = {}
    for k in range(5):  # interleaved so the original order is not already grouped
        for block, factor in (("a", f1), ("b", f2)):
            name = f"{block}{k}"
            names.append(name)
            columns[name] = factor + rng.normal(size=n)
    frame = pd.DataFrame(columns)[names]

    order = cluster_order(correlation_matrix(frame))

    positions = [i for i, name in enumerate(order) if name.startswith("a")]
    assert max(positions) - min(positions) == 4
    assert sorted(order) == sorted(names)


def test_ranking_scales_to_a_wide_universe() -> None:
    rng = np.random.default_rng(8)
    tickers = [f"S{k:04d}" for k in range(1200)]
    frame = pd.DataFrame(rng.normal(size=(300, 1200)), columns=tickers)

    ranked = rank_peers(frame, "S0000", 99)

    assert len(ranked) == 99
    assert "S0000" not in ranked.index
    assert (np.diff(ranked["corr"].to_numpy()) <= 0).all()


# ---- validation ----


def test_residualizer_rejects_bad_inputs() -> None:
    u = make_universe(n_days=300)
    bad = u.returns.copy()
    bad.iloc[10, 0] = np.nan
    model = fit_residualizer(u.returns, u.market, u.sector_returns, u.sector_of)

    with pytest.raises(ValueError, match="NaN"):
        fit_residualizer(bad, u.market, u.sector_returns, u.sector_of)
    with pytest.raises(ValueError, match="same index"):
        fit_residualizer(u.returns, u.market.iloc[1:], u.sector_returns, u.sector_of)
    with pytest.raises(ValueError, match="no sector given"):
        fit_residualizer(u.returns, u.market, u.sector_returns, {"T": "A"})
    with pytest.raises(ValueError, match="no sector return series"):
        fit_residualizer(u.returns, u.market, u.sector_returns[["A"]], u.sector_of)
    with pytest.raises(ValueError, match="at least"):
        fit_residualizer(
            u.returns.iloc[:20], u.market.iloc[:20], u.sector_returns.iloc[:20], u.sector_of
        )
    with pytest.raises(ValueError, match="missing"):
        residualize(model, u.returns[["T", "P1"]], u.market, u.sector_returns)


def test_ranking_and_validation_reject_bad_requests() -> None:
    u = make_universe(n_days=300)

    with pytest.raises(ValueError, match="top_k"):
        rank_peers(u.returns, "T", 0)
    with pytest.raises(KeyError):
        correlation_with(u.returns, "NOPE")
    with pytest.raises(ValueError, match="exclude the target"):
        validate_peers(u.returns, u.returns, "T", ["T", "P1"])
    with pytest.raises(ValueError, match="empty"):
        validate_peers(u.returns, u.returns, "T", [])
    with pytest.raises(ValueError, match="at least"):
        correlation_with(u.returns.iloc[:10], "T")
    with pytest.raises(ValueError, match="more than 3"):
        fisher_interval(np.array([0.5]), 3)
    with pytest.raises(ValueError, match="level"):
        fisher_interval(np.array([0.5]), 100, level=1.0)


def test_cluster_order_rejects_bad_matrices() -> None:
    good = pd.DataFrame(np.eye(3), index=list("abc"), columns=list("abc"))
    with_nan = good.copy()
    with_nan.iloc[0, 1] = np.nan
    lopsided = good.copy()
    lopsided.iloc[0, 1] = 0.5

    assert cluster_order(good.iloc[:2, :2]) == ["a", "b"]
    with pytest.raises(ValueError, match="NaN"):
        cluster_order(with_nan)
    with pytest.raises(ValueError, match="symmetric"):
        cluster_order(lopsided)
    with pytest.raises(ValueError, match="square"):
        cluster_order(good.iloc[:2, :])
