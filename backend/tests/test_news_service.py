from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from fakes import FakeNewsProvider, InMemoryNewsStore
from whymoved.domain.news import NewsItem
from whymoved.services.news import DEFAULT_OVERLAP, collect_news, merge_news

HISTORY = datetime(2026, 10, 1)
END = datetime(2026, 10, 30)


def item(
    id_: int,
    created: datetime,
    *,
    updated: datetime | None = None,
    symbols: tuple[str, ...] = ("AAA",),
    headline: str | None = None,
) -> NewsItem:
    return NewsItem(
        id=id_,
        headline=headline or f"headline {id_}",
        source="benzinga",
        url=f"https://example.com/{id_}",
        created_at=created,
        updated_at=updated,
        symbols=symbols,
    )


# ---- merging ----


def test_merge_sorts_by_time_and_removes_nothing_else() -> None:
    existing = [item(2, datetime(2026, 10, 12)), item(1, datetime(2026, 10, 10))]
    fresh = [item(3, datetime(2026, 10, 11))]

    merged = merge_news(existing, fresh)

    assert [i.id for i in merged] == [1, 3, 2]


def test_the_later_version_wins_and_ties_go_to_the_fresh_one() -> None:
    created = datetime(2026, 10, 10)
    old = item(1, created, updated=created + timedelta(hours=1), headline="old")
    newer = item(1, created, updated=created + timedelta(hours=2), headline="new")
    stale = item(1, created, updated=created + timedelta(minutes=30), headline="stale")
    tie = item(1, created, updated=created + timedelta(hours=1), headline="tie")

    assert merge_news([old], [newer])[0].headline == "new"
    assert merge_news([old], [stale])[0].headline == "old"
    assert merge_news([old], [tie])[0].headline == "tie"


def test_merge_drops_headlines_before_the_cutoff() -> None:
    items = [item(1, datetime(2026, 9, 15)), item(2, datetime(2026, 10, 15))]

    merged = merge_news(items, [], cutoff=HISTORY)

    assert [i.id for i in merged] == [2]


# ---- collecting ----


def test_the_first_run_fetches_the_whole_window() -> None:
    items = [
        item(1, datetime(2026, 9, 20)),  # before the window
        item(2, datetime(2026, 10, 5)),
        item(3, datetime(2026, 10, 20), symbols=("BBB",)),
    ]
    provider, store = FakeNewsProvider(items), InMemoryNewsStore()

    merged, report = collect_news(provider, store, ["AAA", "BBB"], history_start=HISTORY, end=END)

    assert provider.calls == [(("AAA", "BBB"), HISTORY, END)]
    assert [i.id for i in merged] == [2, 3]
    assert [i.id for i in store.items] == [2, 3]
    assert store.saves == 1
    assert (report.n_total, report.n_fetched, report.n_new) == (2, 2, 2)
    assert report.forced is False


def test_the_second_run_starts_at_the_newest_headline_minus_the_overlap() -> None:
    first = item(2, datetime(2026, 10, 5))
    second = item(3, datetime(2026, 10, 20))
    provider = FakeNewsProvider([first, second, item(4, datetime(2026, 10, 29))])
    store = InMemoryNewsStore([first, second])

    merged, report = collect_news(provider, store, ["AAA"], history_start=HISTORY, end=END)

    assert provider.calls[0][1] == datetime(2026, 10, 20) - DEFAULT_OVERLAP
    assert [i.id for i in merged] == [2, 3, 4]
    assert report.n_fetched == 2  # the overlap re-fetches id 3, plus the new id 4
    assert report.n_new == 1
    assert report.n_total == 3


def test_headlines_older_than_the_window_are_dropped() -> None:
    old = item(1, datetime(2026, 9, 15))
    recent = item(2, datetime(2026, 10, 20))
    provider = FakeNewsProvider([recent])
    store = InMemoryNewsStore([old, recent])

    merged, _ = collect_news(provider, store, ["AAA"], history_start=HISTORY, end=END)

    assert [i.id for i in merged] == [2]


def test_force_full_ignores_what_is_stored() -> None:
    stale = item(99, datetime(2026, 10, 10))
    current = item(2, datetime(2026, 10, 12))
    provider = FakeNewsProvider([current])
    store = InMemoryNewsStore([stale])

    merged, report = collect_news(
        provider, store, ["AAA"], history_start=HISTORY, end=END, force_full=True
    )

    assert provider.calls[0][1] == HISTORY
    assert [i.id for i in merged] == [2]
    assert report.forced is True


def test_nothing_is_fetched_when_the_window_is_empty() -> None:
    future = item(1, datetime(2026, 11, 5))
    provider = FakeNewsProvider([future])
    store = InMemoryNewsStore([future])

    _, report = collect_news(provider, store, ["AAA"], history_start=HISTORY, end=END)

    assert provider.calls == []
    assert report.n_fetched == 0


def test_invalid_requests_are_rejected() -> None:
    provider, store = FakeNewsProvider([]), InMemoryNewsStore()

    with pytest.raises(ValueError, match="empty"):
        collect_news(provider, store, [], history_start=HISTORY, end=END)
    with pytest.raises(ValueError, match="unique"):
        collect_news(provider, store, ["AAA", "AAA"], history_start=HISTORY, end=END)
    with pytest.raises(ValueError, match="history_start"):
        collect_news(provider, store, ["AAA"], history_start=END, end=HISTORY)
    with pytest.raises(ValueError, match="overlap"):
        collect_news(provider, store, ["AAA"], history_start=HISTORY, end=END, overlap=timedelta(0))


def test_a_new_symbol_gets_its_full_history_while_the_rest_stay_incremental() -> None:
    stored = item(2, datetime(2026, 10, 20))
    old_pltr = item(7, datetime(2026, 10, 3), symbols=("PLTR",))
    provider = FakeNewsProvider(
        [stored, old_pltr, item(8, datetime(2026, 10, 29), symbols=("PLTR",))]
    )
    store = InMemoryNewsStore([stored])

    merged, _ = collect_news(
        provider, store, ["AAA", "PLTR"], history_start=HISTORY, end=END, backfill=["PLTR"]
    )

    assert provider.calls == [
        (("AAA",), datetime(2026, 10, 20) - DEFAULT_OVERLAP, END),
        (("PLTR",), HISTORY, END),
    ]
    assert [i.id for i in merged] == [7, 2, 8]  # PLTR's older headline is filled in


def test_backfill_is_ignored_on_a_first_run_that_fetches_everything_anyway() -> None:
    provider = FakeNewsProvider([])

    collect_news(
        provider,
        InMemoryNewsStore([]),
        ["AAA", "PLTR"],
        history_start=HISTORY,
        end=END,
        backfill=["PLTR"],
    )

    assert provider.calls == [(("AAA", "PLTR"), HISTORY, END)]
