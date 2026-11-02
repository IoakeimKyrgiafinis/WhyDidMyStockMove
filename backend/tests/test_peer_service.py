from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from helpers import as_float
from peer_market import COINTEGRATED, LINKED, make_peer_snapshot
from whymoved.services.explain import MoveExplainer, NotEnoughData
from whymoved.services.peers import REPLAY_WINDOW, VALIDATION_DAYS


@pytest.fixture(scope="module")
def explainer() -> MoveExplainer:
    return MoveExplainer(make_peer_snapshot(), n_boot=200)


def test_the_planted_peers_rank_first(explainer: MoveExplainer) -> None:
    result = explainer.peers.peers("AAA")

    top = set(result.ranking.index[:6])
    assert top == {*LINKED, COINTEGRATED}
    assert "LATE" not in result.ranking.index  # incomplete history is left out
    assert len(result.ranking) == len(result.window.selection.columns) - 1


def test_real_links_hold_up_out_of_sample_and_noise_does_not(explainer: MoveExplainer) -> None:
    v = explainer.peers.peers("AAA").validation

    linked = v.loc[list(LINKED)]
    assert (linked["corr_validation"] > 0.15).all()
    assert linked["corr_validation"].mean() == pytest.approx(
        linked["corr_selection"].mean(), abs=0.12
    )
    # The best-looking unrelated stock regresses towards zero: the winner's curse.
    noise = v.drop(index=[*LINKED, COINTEGRATED])
    best = str(noise["corr_selection"].idxmax())
    assert as_float(noise.loc[best, "corr_validation"]) < as_float(
        noise.loc[best, "corr_selection"]
    )


def test_grid_layers_are_square_and_ordered(explainer: MoveExplainer) -> None:
    result = explainer.peers.peers("AAA")
    n = len(result.order)

    assert sorted(result.order) == sorted(["AAA", *result.ranking.index])
    for layer in ("residual", "raw", "spillover"):
        assert result.grids[layer].shape == (n, n)
    residual = result.grids["residual"]
    assert np.allclose(np.diag(residual), 1.0)
    assert np.allclose(residual, residual.T)
    # raw correlations include the market, so they are larger on average
    upper = np.triu_indices(n, k=1)
    assert result.grids["raw"][upper].mean() > residual[upper].mean() + 0.2
    # clustering puts the linked stocks next to each other
    positions = sorted(result.order.index(t) for t in ["AAA", *LINKED, COINTEGRATED])
    assert positions[-1] - positions[0] == len(positions) - 1


def test_spillover_beta_matches_the_planted_link(explainer: MoveExplainer) -> None:
    s = explainer.peers.peers("AAA").spillover

    # cov(link part) / var(AAA residual) = 0.64 * 0.006^2 / (0.64 * 0.006^2 + 0.008^2)
    expected = 0.64 * 0.006**2 / (0.64 * 0.006**2 + 0.008**2)
    assert s.loc[list(LINKED), "beta"].mean() == pytest.approx(expected, abs=0.08)
    assert (s["se"] > 0).all()


def test_only_the_planted_pair_is_cointegrated(explainer: MoveExplainer) -> None:
    table = explainer.peers.cointegration_table("AAA")

    assert bool(table.loc[COINTEGRATED, "cointegrated"])
    assert table.loc[COINTEGRATED, "hedge_ratio"] == pytest.approx(1.0, abs=0.15)
    others = table.drop(index=COINTEGRATED)
    assert int(others["cointegrated"].sum()) <= 1
    assert (table["q_value"] >= table["p_value"] - 1e-12).all()


def test_comove_compares_each_peer_with_its_spillover(explainer: MoveExplainer) -> None:
    daily = explainer.daily("AAA")
    day = pd.Timestamp(daily["unexplained"].abs().idxmax()).date()  # type: ignore[arg-type]

    c = explainer.peers.comove("AAA", day)

    own = as_float(daily.loc[pd.Timestamp(day), "unexplained"])
    assert len(c) == 10
    for ticker, row in c.iterrows():
        assert row["expected"] == pytest.approx(row["beta"] * own)
        actual = as_float(explainer.daily(str(ticker)).loc[pd.Timestamp(day), "unexplained"])
        assert row["actual"] == pytest.approx(actual)


def test_comove_rejects_days_without_an_explanation(explainer: MoveExplainer) -> None:
    with pytest.raises(NotEnoughData):
        explainer.peers.comove("AAA", explainer.close.index[10].date())


def test_replay_streams_frames_in_date_order(explainer: MoveExplainer) -> None:
    frames: list[dict[str, Any]] = list(explainer.peers.replay("AAA"))

    names = frames[0]["names"]
    assert frames[0]["type"] == "names" and names[0] == "AAA" and len(names) == 20
    body = frames[1:]
    assert all(f["type"] == "frame" for f in body)
    dates = [f["date"] for f in body]
    assert dates == sorted(dates)
    first = body[0]["residual"]
    assert len(first) == 20 and len(first[0]) == 20 and first[0][0] == 1.0
    assert body[0]["mean_raw"] > body[0]["mean_residual"]
    n_days = len(explainer.daily("AAA"))
    assert len(body) == (n_days - REPLAY_WINDOW) // 5 + 1


def test_a_stock_without_full_history_cannot_be_analysed(explainer: MoveExplainer) -> None:
    with pytest.raises(NotEnoughData, match="LATE"):
        explainer.peers.peers("LATE")


def test_windows_are_consecutive(explainer: MoveExplainer) -> None:
    w = explainer.peers.window()

    assert len(w.validation) == VALIDATION_DAYS
    assert w.selection.index[-1] < w.validation.index[0]
    assert w.start < w.split <= w.end
