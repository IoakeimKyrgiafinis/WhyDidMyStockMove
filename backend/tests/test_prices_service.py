from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from fakes import FACTORS, SECTOR_OF, FakePriceProvider, InMemoryStore, make_world
from whymoved.domain.bars import DailyBars
from whymoved.services.prices import changed_symbols, merge_bars, update_prices

HISTORY_START = date(2024, 1, 1)
SYMBOLS = [*FACTORS, *SECTOR_OF]


def day(frame: pd.DataFrame, i: int) -> date:
    return pd.Timestamp(frame.index[i]).date()


def small(values: dict[str, list[float]]) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-02", periods=len(next(iter(values.values()))))
    return pd.DataFrame(values, index=index)


def bars(close: dict[str, list[float]], start: str = "2024-01-02") -> DailyBars:
    length = len(next(iter(close.values())))
    index = pd.bdate_range(start, periods=length, name="date")
    frame = pd.DataFrame(close, index=index)
    return DailyBars(close=frame, volume=frame * 100.0)


# ---- detecting adjustment changes ----


def test_identical_prices_are_not_changes() -> None:
    stored = small({"A": [10.0, 11.0, 12.0], "B": [5.0, 5.5, 6.0]})

    assert changed_symbols(stored, stored.copy()) == []


def test_a_rescaled_history_is_detected() -> None:
    stored = small({"A": [10.0, 11.0, 12.0], "B": [5.0, 5.5, 6.0]})
    fresh = stored.copy()
    fresh["A"] = fresh["A"] * 0.5

    assert changed_symbols(stored, fresh) == ["A"]


def test_tiny_differences_respect_the_tolerance() -> None:
    stored = small({"A": [10.0, 11.0, 12.0]})
    fresh = stored * (1.0 + 1e-9)

    assert changed_symbols(stored, fresh) == []
    assert changed_symbols(stored, fresh, rel_tol=1e-12) == ["A"]


def test_missing_values_and_unknown_symbols_are_ignored() -> None:
    stored = small({"A": [10.0, np.nan, 12.0]})
    fresh = small({"A": [10.0, 99.0, 12.0], "Z": [1.0, 2.0, 3.0]})

    assert changed_symbols(stored, fresh) == []


def test_no_shared_dates_means_no_change() -> None:
    stored = small({"A": [1.0, 2.0, 3.0]})
    fresh = stored.copy()
    fresh.index = fresh.index + pd.Timedelta(days=30)

    assert changed_symbols(stored, fresh) == []


# ---- merging ----


def test_fresh_values_win_and_the_stored_history_fills_the_gaps() -> None:
    existing = bars({"A": [1.0, 2.0, 3.0], "B": [10.0, 20.0, 30.0]})  # Jan 2, 3, 4
    incremental = bars({"A": [2.5, 3.5, 4.5]}, start="2024-01-03")  # Jan 3, 4, 5

    merged = merge_bars(existing, incremental, None, ["A", "B"])

    assert merged.close["A"].tolist() == [1.0, 2.5, 3.5, 4.5]
    assert merged.volume["A"].tolist() == [100.0, 250.0, 350.0, 450.0]
    b = merged.close["B"].tolist()
    assert b[:3] == [10.0, 20.0, 30.0]
    assert bool(np.isnan(b[3]))


def test_a_full_refetch_replaces_the_stored_column_completely() -> None:
    existing = bars({"A": [1.0, 2.0, 3.0], "B": [10.0, 20.0, 30.0]})
    full = bars({"A": [0.5, np.nan, 1.5]})

    merged = merge_bars(existing, None, full, ["A", "B"])

    a = merged.close["A"].tolist()
    assert a[0] == 0.5 and a[2] == 1.5
    assert bool(np.isnan(a[1]))
    assert merged.close["B"].tolist() == [10.0, 20.0, 30.0]


def test_the_result_follows_the_requested_symbols_and_drops_empty_rows() -> None:
    existing = bars({"A": [1.0, 2.0], "OLD": [5.0, 6.0]})
    full = bars({"NEW": [7.0, np.nan, np.nan]})

    merged = merge_bars(existing, None, full, ["A", "NEW"])

    assert list(merged.close.columns) == ["A", "NEW"]
    assert len(merged.close) == 2
    assert merged.volume.index.equals(merged.close.index)
    assert merged.close.index.name == "date"


def test_merging_nothing_is_an_error() -> None:
    with pytest.raises(ValueError, match="nothing"):
        merge_bars(None, None, None, ["A"])


# ---- the update flow ----


def test_the_first_run_fetches_everything_once() -> None:
    close, volume = make_world(300)
    provider, store = FakePriceProvider(close, volume), InMemoryStore()

    merged, report = update_prices(
        provider, store, SYMBOLS, history_start=HISTORY_START, end=day(close, 299)
    )

    assert len(provider.calls) == 1
    names, start, _ = provider.calls[0]
    assert list(names) == SYMBOLS
    assert start == HISTORY_START
    assert store.saves == 1
    assert merged.symbols == SYMBOLS
    assert report.new_symbols == tuple(SYMBOLS)
    assert report.refetched == ()
    assert report.first_date == day(close, 0)
    assert report.last_date == day(close, 299)
    pd.testing.assert_frame_equal(
        merged.close, close.reindex(columns=SYMBOLS), check_freq=False, check_index_type=False
    )


def test_a_normal_night_fetches_only_the_overlap_and_the_new_days() -> None:
    close, volume = make_world(300)
    provider, store = FakePriceProvider(close, volume), InMemoryStore()
    update_prices(provider, store, SYMBOLS, history_start=HISTORY_START, end=day(close, 298))
    provider.calls.clear()

    merged, report = update_prices(
        provider, store, SYMBOLS, history_start=HISTORY_START, end=day(close, 299)
    )

    assert len(provider.calls) == 1
    names, start, _ = provider.calls[0]
    assert list(names) == SYMBOLS
    assert start == day(close, 298) - timedelta(days=10)
    assert report.new_symbols == ()
    assert report.refetched == ()
    pd.testing.assert_frame_equal(
        merged.close, close.reindex(columns=SYMBOLS), check_freq=False, check_index_type=False
    )


def test_a_changed_adjustment_refetches_only_that_symbol() -> None:
    close, volume = make_world(300)
    provider, store = FakePriceProvider(close, volume), InMemoryStore()
    update_prices(provider, store, SYMBOLS, history_start=HISTORY_START, end=day(close, 298))
    provider.calls.clear()
    provider.close["AAA"] = provider.close["AAA"] * 0.5  # a 2-for-1 split rewrites the history

    merged, report = update_prices(
        provider, store, SYMBOLS, history_start=HISTORY_START, end=day(close, 299)
    )

    assert report.refetched == ("AAA",)
    assert len(provider.calls) == 2
    assert list(provider.calls[0][0]) == SYMBOLS
    assert provider.calls[1][0] == ("AAA",)
    assert provider.calls[1][1] == HISTORY_START
    truth = provider.close.reindex(columns=SYMBOLS)
    pd.testing.assert_frame_equal(merged.close, truth, check_freq=False, check_index_type=False)


def test_a_new_symbol_is_fetched_in_full_and_the_others_incrementally() -> None:
    close, volume = make_world(300, extra=("EEE",))
    provider, store = FakePriceProvider(close, volume), InMemoryStore()
    update_prices(provider, store, SYMBOLS, history_start=HISTORY_START, end=day(close, 298))
    provider.calls.clear()

    merged, report = update_prices(
        provider, store, [*SYMBOLS, "EEE"], history_start=HISTORY_START, end=day(close, 299)
    )

    assert report.new_symbols == ("EEE",)
    assert len(provider.calls) == 2
    assert list(provider.calls[0][0]) == SYMBOLS
    assert provider.calls[1][0] == ("EEE",)
    assert provider.calls[1][1] == HISTORY_START
    assert int(merged.close["EEE"].notna().sum()) == 300


def test_force_full_refetches_everything() -> None:
    close, volume = make_world(300)
    provider, store = FakePriceProvider(close, volume), InMemoryStore()
    update_prices(provider, store, SYMBOLS, history_start=HISTORY_START, end=day(close, 298))
    provider.calls.clear()

    _, report = update_prices(
        provider,
        store,
        SYMBOLS,
        history_start=HISTORY_START,
        end=day(close, 299),
        force_full=True,
    )

    assert len(provider.calls) == 1
    names, start, _ = provider.calls[0]
    assert list(names) == SYMBOLS
    assert start == HISTORY_START
    assert report.forced is True


def test_a_symbol_with_no_data_stays_empty_and_is_requested_in_full_again() -> None:
    close, volume = make_world(300)
    provider, store = FakePriceProvider(close, volume), InMemoryStore()
    symbols = [*SYMBOLS, "ZZZ"]

    first, _ = update_prices(
        provider, store, symbols, history_start=HISTORY_START, end=day(close, 298)
    )
    provider.calls.clear()
    update_prices(provider, store, symbols, history_start=HISTORY_START, end=day(close, 299))

    assert bool(first.close["ZZZ"].isna().all())
    assert provider.calls[1][0] == ("ZZZ",)
    assert provider.calls[1][1] == HISTORY_START


def test_invalid_requests_are_rejected() -> None:
    close, volume = make_world(60)
    provider, store = FakePriceProvider(close, volume), InMemoryStore()
    end = day(close, 59)

    with pytest.raises(ValueError, match="empty"):
        update_prices(provider, store, [], history_start=HISTORY_START, end=end)
    with pytest.raises(ValueError, match="unique"):
        update_prices(provider, store, ["A", "A"], history_start=HISTORY_START, end=end)
    with pytest.raises(ValueError, match="history_start"):
        update_prices(provider, store, ["AAA"], history_start=end + timedelta(days=1), end=end)
    with pytest.raises(ValueError, match="overlap_days"):
        update_prices(
            provider, store, ["AAA"], history_start=HISTORY_START, end=end, overlap_days=0
        )
