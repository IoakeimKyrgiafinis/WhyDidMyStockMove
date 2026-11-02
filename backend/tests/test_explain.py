from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from market import BETA_MARKET, BETA_SECTOR, SHOCK, make_market, shock_date
from whymoved.services.explain import MoveExplainer, NotEnoughData, UnknownTicker


@pytest.fixture(scope="module")
def explainer() -> MoveExplainer:
    return MoveExplainer(make_market().snapshot, n_boot=200)


def test_search_puts_the_exact_ticker_first(explainer: MoveExplainer) -> None:
    assert [m.ticker for m in explainer.search("abc")] == ["ABC"]
    assert [m.ticker for m in explainer.search("a")][:2] == ["AAA", "ABC"]
    assert [m.ticker for m in explainer.search("bank")] == ["BBB"]
    assert len(explainer.search("")) == 4


def test_unknown_ticker(explainer: MoveExplainer) -> None:
    with pytest.raises(UnknownTicker):
        explainer.daily("ZZZ")


def test_ticker_lookup_ignores_case_and_spaces(explainer: MoveExplainer) -> None:
    assert explainer.member(" aaa ").ticker == "AAA"


def test_daily_parts_add_up(explainer: MoveExplainer) -> None:
    d = explainer.daily("AAA")

    parts = d["market"] + d["sector"] + d["alpha"] + d["unexplained"]
    assert np.allclose(parts, d["total"], atol=1e-12)
    assert len(d) == 699 - explainer.window


def test_the_planted_shock_lands_in_the_unexplained_part_and_is_flagged(
    explainer: MoveExplainer,
) -> None:
    when = shock_date(make_market())
    day, row = explainer.day("AAA", when)

    assert day.unexplained == pytest.approx(SHOCK, abs=0.03)
    assert bool(row["flagged"]) is True
    assert float(row["z"]) < -4.0


def test_day_matches_the_daily_table(explainer: MoveExplainer) -> None:
    d = explainer.daily("BBB")
    when = d.index[100].date()

    day, _ = explainer.day("BBB", when)

    assert day.market == pytest.approx(float(d["market"].iloc[100]), abs=1e-9)
    assert day.unexplained == pytest.approx(float(d["unexplained"].iloc[100]), abs=1e-9)


def test_a_day_without_enough_history_is_rejected(explainer: MoveExplainer) -> None:
    with pytest.raises(NotEnoughData):
        explainer.day("AAA", date(2023, 1, 3))


def test_a_new_listing_cannot_be_explained(explainer: MoveExplainer) -> None:
    with pytest.raises(NotEnoughData, match="NEW"):
        explainer.daily("NEW")
    with pytest.raises(NotEnoughData, match="NEW"):
        explainer.profile("NEW")


def test_attribution_parts_sum_to_the_sum_of_days(explainer: MoveExplainer) -> None:
    a = explainer.attribution("AAA", None, None)

    assert a.market + a.sector + a.drift + a.unexplained == pytest.approx(a.sum_of_days)
    assert a.n_days == len(explainer.daily("AAA"))
    scored = int(explainer.daily("AAA")["p_value"].notna().sum())
    assert a.expected_flags == pytest.approx(explainer.alpha * scored)


def test_attribution_compounds_the_real_price_change(explainer: MoveExplainer) -> None:
    d = explainer.daily("AAA")
    start, end = d.index[10], d.index[60]
    prices = explainer.prices("AAA")["stock"]

    a = explainer.attribution("AAA", start.date(), end.date())

    before = prices.index[int(prices.index.get_indexer(pd.Index([start]))[0]) - 1]
    expected = float(prices[end] / prices[before] - 1.0)
    assert a.compounded == pytest.approx(expected, rel=1e-9)
    assert a.n_days == 51


def test_attribution_rejects_bad_ranges(explainer: MoveExplainer) -> None:
    with pytest.raises(ValueError, match="start"):
        explainer.attribution("AAA", date(2025, 1, 1), date(2024, 1, 1))
    with pytest.raises(NotEnoughData):
        explainer.attribution("AAA", date(2030, 1, 1), date(2030, 2, 1))


def test_profile_recovers_the_planted_betas(explainer: MoveExplainer) -> None:
    p = explainer.profile("BBB")

    assert p.fit.beta_market == pytest.approx(BETA_MARKET, abs=0.2)
    assert p.fit.beta_sector == pytest.approx(BETA_SECTOR, abs=0.25)
    assert p.variance.shares.sum() == pytest.approx(1.0)
    assert p.health.status in ("good", "caution", "poor")
    assert p.last_date == make_market().close.index[-1].date()


def test_results_are_cached(explainer: MoveExplainer) -> None:
    assert explainer.daily("AAA") is explainer.daily("AAA")


def test_a_snapshot_without_the_market_factor_is_rejected() -> None:
    snapshot = make_market().snapshot
    broken = type(snapshot)(
        close=snapshot.close.drop(columns=["IWV"]),
        members=snapshot.members,
        as_of=snapshot.as_of,
        history_start=snapshot.history_start,
    )

    with pytest.raises(ValueError, match="IWV"):
        MoveExplainer(broken)


def test_members_without_prices_are_dropped() -> None:
    snapshot = make_market().snapshot
    trimmed = type(snapshot)(
        close=snapshot.close.drop(columns=["BBB"]),
        members=snapshot.members,
        as_of=snapshot.as_of,
        history_start=snapshot.history_start,
    )

    assert "BBB" not in [m.ticker for m in MoveExplainer(trimmed).members]


def test_returns_skip_days_when_any_series_is_missing() -> None:
    snapshot = make_market().snapshot
    close = snapshot.close.copy()
    close.loc[close.index[300], "XLK"] = np.nan
    explainer = MoveExplainer(
        type(snapshot)(
            close=close,
            members=snapshot.members,
            as_of=snapshot.as_of,
            history_start=snapshot.history_start,
        )
    )

    assert close.index[300] not in explainer.prices("AAA").index
    assert isinstance(explainer.returns("AAA"), pd.DataFrame)
