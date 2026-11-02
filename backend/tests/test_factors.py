from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from helpers import NOISE_STD, TRUE_BETA_MARKET, TRUE_BETA_SECTOR, make_panel
from whymoved.domain.factors import (
    explain_day,
    fit_factor_model,
    orthogonalize_sector,
    rolling_decomposition,
)


def test_orthogonalized_sector_is_uncorrelated_with_market() -> None:
    _, market, sector = make_panel(n=500).arrays()

    _, _, pure = orthogonalize_sector(sector, market)

    assert abs(float(np.corrcoef(pure, market)[0, 1])) < 1e-10


def test_fit_recovers_planted_betas() -> None:
    fit = fit_factor_model(*make_panel(n=2000).arrays())

    assert fit.beta_market == pytest.approx(TRUE_BETA_MARKET, abs=0.15)
    assert fit.beta_sector == pytest.approx(TRUE_BETA_SECTOR, abs=0.15)
    assert 0.6 < fit.r_squared < 0.85


def test_hac_standard_error_is_close_to_analytic_for_iid_noise() -> None:
    stock, market, sector = make_panel(n=2000).arrays()

    fit = fit_factor_model(stock, market, sector)

    analytic = NOISE_STD / (float(np.std(market)) * np.sqrt(len(market)))
    ratio = fit.se_beta_market / analytic
    assert 0.6 < ratio < 1.6


def test_parts_sum_to_total() -> None:
    panel = make_panel(n=400)
    date = panel.stock.index[320]

    day = explain_day(panel.stock, panel.market, panel.sector, date)

    total = day.alpha + day.market + day.sector + day.unexplained
    assert total == pytest.approx(day.total, abs=1e-12)
    assert day.se_explained > 0


def test_explain_day_recovers_planted_shock() -> None:
    panel = make_panel(n=400)
    date = panel.stock.index[300]
    shocked = panel.stock.copy()
    shocked.iloc[300] -= 0.05

    base = explain_day(panel.stock, panel.market, panel.sector, date)
    with_shock = explain_day(shocked, panel.market, panel.sector, date)

    # The fit uses only earlier days, so only the unexplained part can change.
    assert with_shock.unexplained - base.unexplained == pytest.approx(-0.05, abs=1e-9)
    assert with_shock.market == pytest.approx(base.market)
    assert with_shock.sector == pytest.approx(base.sector)


def test_explain_day_ignores_future_data() -> None:
    panel = make_panel(n=400)
    date = panel.stock.index[300]
    base = explain_day(panel.stock, panel.market, panel.sector, date)

    stock2 = panel.stock.copy()
    market2 = panel.market.copy()
    sector2 = panel.sector.copy()
    stock2.iloc[301:] += 0.5
    market2.iloc[301:] -= 0.5
    sector2.iloc[301:] += 0.5

    changed = explain_day(stock2, market2, sector2, date)

    assert changed.fit.beta_market == pytest.approx(base.fit.beta_market)
    assert changed.fit.beta_sector == pytest.approx(base.fit.beta_sector)
    assert changed.alpha == pytest.approx(base.alpha)
    assert changed.market == pytest.approx(base.market)
    assert changed.sector == pytest.approx(base.sector)
    assert changed.unexplained == pytest.approx(base.unexplained)


def test_rolling_matches_explain_day() -> None:
    panel = make_panel(n=400)
    rolled = rolling_decomposition(panel.stock, panel.market, panel.sector)
    pos = 350
    day = explain_day(panel.stock, panel.market, panel.sector, panel.stock.index[pos])
    row = pos - 252

    assert len(rolled) == 400 - 252
    assert rolled.index[0] == panel.stock.index[252]
    assert rolled["total"].to_numpy()[row] == pytest.approx(day.total, abs=1e-9)
    assert rolled["market"].to_numpy()[row] == pytest.approx(day.market, abs=1e-9)
    assert rolled["sector"].to_numpy()[row] == pytest.approx(day.sector, abs=1e-9)
    assert rolled["alpha"].to_numpy()[row] == pytest.approx(day.alpha, abs=1e-9)
    assert rolled["unexplained"].to_numpy()[row] == pytest.approx(day.unexplained, abs=1e-9)


def test_explain_day_requires_enough_history() -> None:
    panel = make_panel(n=400)

    with pytest.raises(ValueError, match="history"):
        explain_day(panel.stock, panel.market, panel.sector, panel.stock.index[100])


def test_explain_day_unknown_date() -> None:
    panel = make_panel(n=400)

    with pytest.raises(KeyError):
        explain_day(panel.stock, panel.market, panel.sector, pd.Timestamp("1999-01-04"))


def test_unsorted_index_is_rejected() -> None:
    panel = make_panel(n=400)

    with pytest.raises(ValueError, match="sorted"):
        rolling_decomposition(
            panel.stock.iloc[::-1], panel.market.iloc[::-1], panel.sector.iloc[::-1]
        )
