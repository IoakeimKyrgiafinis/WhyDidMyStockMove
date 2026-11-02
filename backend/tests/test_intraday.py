from __future__ import annotations

import numpy as np
import pytest

from intraday_market import BETA_MARKET, BETA_SECTOR, at, make_bars, slot
from whymoved.domain.intraday import (
    bucket_of,
    fit_intraday_model,
    group_headlines,
    prepare_sessions,
    score_reaction,
)


def test_sessions_are_in_new_york_time_and_skip_the_overnight_bar() -> None:
    closes, days = make_bars(n_days=12)

    data = prepare_sessions(closes)

    assert data.sessions == days
    first = data.intraday[data.intraday["day"] == days[0]]
    assert first.index[0].hour == 9 and first.index[0].minute == 35  # 9:30 has no return
    assert first["slot"].iloc[0] == 1
    assert len(first) == 77
    assert len(data.overnight) == 11  # every session but the first


def test_thin_sessions_are_dropped() -> None:
    closes, days = make_bars(n_days=12)
    thin = closes.index.isin(closes.index[(closes.index.date == days[5])][::2])  # type: ignore[attr-defined]
    closes.loc[thin, "stock"] = np.nan

    data = prepare_sessions(closes)

    assert days[5] not in data.sessions
    assert len(data.sessions) == 11


def test_the_model_recovers_betas_and_the_u_shape() -> None:
    closes, days = make_bars()
    data = prepare_sessions(closes)

    model = fit_intraday_model(data, before=days[-1])

    assert model.beta_market == pytest.approx(BETA_MARKET, abs=0.1)
    assert model.beta_sector == pytest.approx(BETA_SECTOR, abs=0.15)
    assert model.n_days == 40
    open_sigma = model.bucket_sigma[0]
    midday_sigma = model.bucket_sigma[bucket_of(slot(12, 30))]
    assert open_sigma > 1.6 * midday_sigma
    assert model.overnight_sigma is not None


def test_a_planted_reaction_is_found_and_the_quiet_before_is_reported() -> None:
    days = make_bars()[1]
    event = days[-1]
    k = slot(11, 0)
    shocks = {(event, k + j): 0.005 for j in range(6)}  # +3% over the 30 minutes after 11:00
    closes, _ = make_bars(shocks=shocks)
    data = prepare_sessions(closes)
    model = fit_intraday_model(data, before=event)

    reaction = score_reaction(data, model, at(event, 11, 2))

    assert reaction.kind == "intraday"
    assert reaction.after.abnormal == pytest.approx(0.03, abs=0.006)
    assert reaction.after.z > 5
    assert reaction.flagged and reaction.p_empirical == pytest.approx(1 / 41)
    assert reaction.before is not None and abs(reaction.before.z) < 3
    assert not reaction.moved_before
    assert reaction.path_minutes[0] == -30 and 0 in reaction.path_minutes
    gap = reaction.path_actual[-1] - reaction.path_expected[-1]
    assert gap > 0.02


def test_a_move_that_came_before_the_headline_is_flagged_as_such() -> None:
    days = make_bars()[1]
    event = days[-1]
    k = slot(14, 0)
    shocks = {(event, k - 6 + j): -0.004 for j in range(6)}  # the drop happens first ...
    closes, _ = make_bars(shocks=shocks)
    data = prepare_sessions(closes)
    model = fit_intraday_model(data, before=event)

    reaction = score_reaction(data, model, at(event, 14, 1))  # ... then "stock falls" is published

    assert reaction.moved_before
    assert reaction.before is not None and reaction.before.abnormal < -0.015
    assert not reaction.flagged


def test_the_model_ignores_the_event_day() -> None:
    days = make_bars()[1]
    event = days[-1]
    plain = prepare_sessions(make_bars()[0])
    shocked = prepare_sessions(make_bars(shocks={(event, 20): 0.2})[0])

    a = fit_intraday_model(plain, before=event)
    b = fit_intraday_model(shocked, before=event)

    assert a.beta_market == pytest.approx(b.beta_market)
    assert a.bucket_sigma == pytest.approx(b.bucket_sigma)


def test_after_hours_headlines_use_the_overnight_move() -> None:
    days = make_bars()[1]
    closes, _ = make_bars(overnight_shocks={days[-1]: -0.05})
    data = prepare_sessions(closes)
    model = fit_intraday_model(data, before=days[-1])

    evening = score_reaction(data, model, at(days[-2], 17, 45))
    morning = score_reaction(data, model, at(days[-1], 7, 0))

    for reaction in (evening, morning):
        assert reaction.kind == "overnight"
        assert reaction.session == days[-1]
        assert reaction.after.abnormal == pytest.approx(-0.05, abs=0.015)
        assert reaction.flagged
    assert evening.before is not None  # the last half hour of the previous session
    assert morning.before is None


def test_not_enough_history() -> None:
    closes, days = make_bars(n_days=8)
    with pytest.raises(ValueError, match="earlier sessions"):
        fit_intraday_model(prepare_sessions(closes), before=days[-1])


def test_a_headline_at_the_last_bar_still_gets_a_short_window() -> None:
    closes, days = make_bars()
    data = prepare_sessions(closes)
    model = fit_intraday_model(data, before=days[-1])

    reaction = score_reaction(data, model, at(days[-1], 15, 57))

    assert reaction.after.minutes == 5


def test_headlines_close_together_are_grouped() -> None:
    day = make_bars(n_days=2)[1][-1]
    times = [at(day, 10, 0), at(day, 10, 20), at(day, 10, 5), at(day, 10, 14), at(day, 10, 40)]

    assert group_headlines(times) == [[0, 2, 3], [1], [4]]
