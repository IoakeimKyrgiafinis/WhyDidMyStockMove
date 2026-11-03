from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from helpers import as_float, as_int
from whymoved.domain.quality import (
    QualityConfig,
    _longest_run,
    assess_quality,
    drop_near_duplicates,
)

N = 400


def calendar() -> pd.DatetimeIndex:
    return pd.bdate_range("2023-01-02", periods=N)


def walk(rng: np.random.Generator, start: float) -> np.ndarray:
    return start * np.exp(np.cumsum(rng.normal(0.0, 0.01, N)))


def make_closes() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    ipo = walk(rng, 40.0)
    ipo[:300] = np.nan
    gappy = walk(rng, 60.0)
    gappy[150:160] = np.nan
    split = walk(rng, 80.0)
    split[200:] *= 0.2
    nonpositive = walk(rng, 25.0)
    nonpositive[50] = 0.0
    return pd.DataFrame(
        {
            "GOOD": walk(rng, 50.0),
            "IPO": ipo,
            "GAPPY": gappy,
            "PENNY": walk(rng, 1.5),
            "SPLIT": split,
            "STALE": np.repeat(walk(rng, 30.0)[::5], 5)[:N],
            "NONPOS": nonpositive,
        },
        index=calendar(),
    )


def reasons(report: pd.DataFrame, ticker: str) -> str:
    return str(report.loc[ticker, "reasons"])


def test_a_clean_stock_passes() -> None:
    report = assess_quality(make_closes(), calendar())

    assert bool(report.loc["GOOD", "passed"]) is True
    assert reasons(report, "GOOD") == ""
    assert as_float(report.loc["GOOD", "coverage"]) == pytest.approx(1.0)


def test_a_short_history_fails_on_length_not_coverage() -> None:
    report = assess_quality(make_closes(), calendar())

    assert bool(report.loc["IPO", "passed"]) is False
    assert as_float(report.loc["IPO", "coverage"]) == pytest.approx(1.0)  # counted from listing
    assert as_int(report.loc["IPO", "longest_gap"]) == 0
    assert "days of history" in reasons(report, "IPO")
    assert "coverage" not in reasons(report, "IPO")


def test_a_recent_listing_with_enough_history_passes() -> None:
    report = assess_quality(make_closes(), calendar(), QualityConfig(min_history_days=100))

    assert bool(report.loc["IPO", "passed"]) is True


def test_a_stock_that_stops_trading_still_fails() -> None:
    closes = make_closes()
    closes.loc[closes.index[-50:], "GOOD"] = np.nan  # delisted 50 days ago

    report = assess_quality(closes, calendar())

    assert bool(report.loc["GOOD", "passed"]) is False
    assert as_int(report.loc["GOOD", "longest_gap"]) == 50


def test_a_long_gap_fails_even_when_coverage_is_fine() -> None:
    report = assess_quality(make_closes(), calendar())

    assert bool(report.loc["GAPPY", "passed"]) is False
    assert as_int(report.loc["GAPPY", "longest_gap"]) == 10
    assert "in a row" in reasons(report, "GAPPY")
    assert "coverage" not in reasons(report, "GAPPY")


def test_penny_stocks_fail_the_price_floor() -> None:
    report = assess_quality(make_closes(), calendar())

    assert bool(report.loc["PENNY", "passed"]) is False
    assert "median price" in reasons(report, "PENNY")


def test_stale_prices_are_caught() -> None:
    report = assess_quality(make_closes(), calendar())

    assert bool(report.loc["STALE", "passed"]) is False
    assert as_float(report.loc["STALE", "zero_return_share"]) > 0.7
    assert "unchanged price" in reasons(report, "STALE")


def test_non_positive_prices_fail() -> None:
    report = assess_quality(make_closes(), calendar())

    assert bool(report.loc["NONPOS", "passed"]) is False
    assert "non-positive" in reasons(report, "NONPOS")


def test_a_split_sized_jump_fails_under_a_strict_limit() -> None:
    closes = make_closes()

    default = assess_quality(closes, calendar())
    strict = assess_quality(closes, calendar(), QualityConfig(max_extreme_moves=0))

    assert as_int(default.loc["SPLIT", "extreme_moves"]) == 1
    assert bool(default.loc["SPLIT", "passed"]) is True
    assert bool(strict.loc["SPLIT", "passed"]) is False
    assert "daily moves" in reasons(strict, "SPLIT")
    assert bool(strict.loc["GOOD", "passed"]) is True


def test_dates_outside_the_calendar_are_ignored() -> None:
    closes = make_closes()[["GOOD"]]

    report = assess_quality(closes, calendar()[:200])

    assert as_int(report.loc["GOOD", "n_obs"]) == 200
    assert as_float(report.loc["GOOD", "coverage"]) == pytest.approx(1.0)


def test_longest_run_helper() -> None:
    mask = np.array([False, True, True, False, True, True, True, False, True])

    assert _longest_run(mask) == 3
    assert _longest_run(np.zeros(5, dtype=np.bool_)) == 0
    assert _longest_run(np.ones(5, dtype=np.bool_)) == 5


def test_invalid_inputs_are_rejected() -> None:
    closes = make_closes()

    with pytest.raises(ValueError, match="empty"):
        assess_quality(closes, calendar()[:0])
    with pytest.raises(ValueError, match="sorted"):
        assess_quality(closes, calendar()[::-1])
    with pytest.raises(ValueError, match="DatetimeIndex"):
        assess_quality(closes.reset_index(drop=True), calendar())
    with pytest.raises(ValueError, match="no columns"):
        assess_quality(closes.iloc[:, :0], calendar())
    with pytest.raises(ValueError, match="duplicate"):
        assess_quality(closes.iloc[[0, 0, 1]], calendar())
    with pytest.raises(ValueError, match="max_gap_days"):
        QualityConfig(max_gap_days=-1)
    with pytest.raises(ValueError, match="min_coverage"):
        QualityConfig(min_coverage=1.5)


# ---- near-duplicate removal ----


def make_returns() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    n = 300
    base = rng.normal(0.0, 0.01, n)
    distinct = rng.normal(0.0, 0.01, n)
    return pd.DataFrame(
        {
            "GOOGL": base,
            "AAPL": rng.normal(0.0, 0.01, n),
            "GOOG": base + rng.normal(0.0, 0.0005, n),  # correlation about 0.999
            "V": distinct + rng.normal(0.0, 0.005, n),
            "MA": distinct + rng.normal(0.0, 0.005, n),  # correlation about 0.8, a real pair
            "XOM": rng.normal(0.0, 0.01, n),
        }
    )


def test_share_classes_are_dropped_in_favour_of_the_higher_priority() -> None:
    returns = make_returns()
    priority = ["GOOGL", "AAPL", "GOOG", "V", "MA", "XOM"]

    kept, dropped = drop_near_duplicates(returns, priority)

    assert dropped == {"GOOG": "GOOGL"}
    assert kept == ["GOOGL", "AAPL", "V", "MA", "XOM"]


def test_priority_decides_which_class_survives() -> None:
    returns = make_returns()

    kept, dropped = drop_near_duplicates(returns, ["GOOG", "GOOGL", "XOM"])

    assert kept == ["GOOG", "XOM"]
    assert dropped == {"GOOGL": "GOOG"}


def test_a_lower_threshold_removes_strongly_related_stocks_too() -> None:
    returns = make_returns()

    kept, dropped = drop_near_duplicates(returns, ["V", "MA", "XOM"], threshold=0.7)

    assert kept == ["V", "XOM"]
    assert dropped == {"MA": "V"}


def test_duplicate_removal_rejects_bad_requests() -> None:
    returns = make_returns()

    with pytest.raises(ValueError, match="duplicates"):
        drop_near_duplicates(returns, ["AAPL", "AAPL"])
    with pytest.raises(ValueError, match="unknown"):
        drop_near_duplicates(returns, ["AAPL", "NOPE"])
    with pytest.raises(ValueError, match="threshold"):
        drop_near_duplicates(returns, ["AAPL", "XOM"], threshold=0.0)
