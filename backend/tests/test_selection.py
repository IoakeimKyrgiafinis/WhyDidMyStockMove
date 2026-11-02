from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fakes import FACTORS, SECTOR_OF, make_world
from whymoved.domain.bars import DailyBars
from whymoved.domain.quality import trading_calendar
from whymoved.domain.selection import DataQualityError, select_universe
from whymoved.domain.universe import MARKET_ETF

STOCKS = list(SECTOR_OF)


def world_bars() -> DailyBars:
    close, volume = make_world(300)
    return DailyBars(close=close, volume=volume)


# ---- the trading calendar ----


def test_the_calendar_keeps_days_most_symbols_traded() -> None:
    index = pd.bdate_range("2024-01-01", periods=10)
    closes = pd.DataFrame(1.0, index=index, columns=["A", "B", "C", "D"])
    closes["DEAD"] = np.nan  # a symbol with no data does not count
    closes.iloc[3, :4] = np.nan  # nobody traded
    closes.iloc[5, :3] = np.nan  # one of four traded
    closes.iloc[7, :1] = np.nan  # three of four traded

    calendar = trading_calendar(closes)

    assert index[3] not in calendar
    assert index[5] not in calendar
    assert index[7] in calendar
    assert len(calendar) == 8


def test_the_calendar_rejects_bad_input() -> None:
    index = pd.bdate_range("2024-01-01", periods=5)
    good = pd.DataFrame(1.0, index=index, columns=["A"])

    with pytest.raises(ValueError, match="min_share"):
        trading_calendar(good, min_share=0.0)
    with pytest.raises(ValueError, match="DatetimeIndex"):
        trading_calendar(good.reset_index(drop=True))
    with pytest.raises(ValueError, match="no symbol"):
        trading_calendar(good * np.nan)


# ---- selecting the universe ----


def test_clean_stocks_are_kept_and_the_share_class_clone_is_dropped() -> None:
    selection = select_universe(world_bars(), STOCKS, FACTORS)

    assert selection.tickers[0] == "AAA"
    assert set(selection.tickers) == {"AAA", "BBB", "CCC", "DDD"}
    assert selection.duplicates == {"DUP": "AAA"}
    assert selection.failed == {}
    assert MARKET_ETF in selection.quality.index
    assert len(selection.calendar) == 300


def test_liquidity_decides_which_share_class_survives() -> None:
    bars = world_bars()
    volume = bars.volume.copy()
    volume["DUP"] = 100_000_000.0
    swapped = DailyBars(close=bars.close, volume=volume)

    selection = select_universe(swapped, STOCKS, FACTORS)

    assert selection.duplicates == {"AAA": "DUP"}
    assert "DUP" in selection.tickers


def test_a_stock_with_too_little_history_is_reported_and_left_out() -> None:
    bars = world_bars()
    close = bars.close.copy()
    close.loc[close.index[:200], "CCC"] = np.nan

    selection = select_universe(DailyBars(close=close, volume=bars.volume), STOCKS, FACTORS)

    assert "CCC" not in selection.tickers
    assert "coverage" in selection.failed["CCC"]


def test_a_failing_factor_stops_the_job() -> None:
    bars = world_bars()
    close = bars.close.copy()
    close[MARKET_ETF] = np.nan

    with pytest.raises(DataQualityError, match=MARKET_ETF):
        select_universe(DailyBars(close=close, volume=bars.volume), STOCKS, FACTORS)


def test_no_clean_stocks_gives_an_empty_selection() -> None:
    bars = world_bars()
    close = bars.close.copy()
    close[STOCKS] = np.nan

    selection = select_universe(DailyBars(close=close, volume=bars.volume), STOCKS, FACTORS)

    assert selection.tickers == []
    assert set(selection.failed) == set(STOCKS)


def test_missing_columns_and_bad_parameters_are_rejected() -> None:
    bars = world_bars()
    without_aaa = DailyBars(
        close=bars.close.drop(columns=["AAA"]), volume=bars.volume.drop(columns=["AAA"])
    )

    with pytest.raises(ValueError, match="missing"):
        select_universe(without_aaa, STOCKS, FACTORS)
    with pytest.raises(ValueError, match="liquidity_days"):
        select_universe(bars, STOCKS, FACTORS, liquidity_days=0)
