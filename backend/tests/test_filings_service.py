from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from fakes import FakeFilings, make_insider_filing, make_insider_tx, make_ref
from whymoved.adapters.edgar_errors import EdgarBlocked, EdgarFormatError, EdgarNotFound
from whymoved.domain.corporate_events import normalize_events
from whymoved.domain.filings import FilingFormatError, FilingNotFound
from whymoved.domain.insider import empty_trades, normalize_trades
from whymoved.services.filings import collect_filings, gap_start, merge_events

FORM4_SINCE = date(2026, 8, 20)
EVENTS_SINCE = date(2025, 11, 2)
CIKS = {"ACME": 3453}


def run(provider: FakeFilings, known: set[str] | None = None, ciks: dict[str, int] | None = None):  # type: ignore[no-untyped-def]
    return collect_filings(
        provider,
        CIKS if ciks is None else ciks,
        known or set(),
        form4_since=FORM4_SINCE,
        events_since=EVENTS_SINCE,
    )


def form4(accession: str, filed: date = date(2026, 8, 28), *, derivative_only: bool = False):  # type: ignore[no-untyped-def]
    ref = make_ref(accession, "4", filed)
    tx = [make_insider_tx("M", derivative=True)] if derivative_only else [make_insider_tx()]
    return ref, make_insider_filing(ref, tx)


# ---- collecting ----


def test_each_company_is_listed_once_from_the_earliest_start() -> None:
    provider = FakeFilings()

    run(provider, ciks={"ACME": 3453, "BETA": 9})

    assert provider.list_calls == [
        (3453, frozenset({"4", "8-K"}), EVENTS_SINCE),
        (9, frozenset({"4", "8-K"}), EVENTS_SINCE),
    ]


def test_new_form_4s_are_fetched_and_flattened() -> None:
    ref, filing = form4("a1")
    provider = FakeFilings(refs={3453: [ref]}, filings={"a1": filing})

    result = run(provider)

    assert provider.fetched == ["a1"]
    assert list(result.trades["accession"]) == ["a1"]
    assert set(result.trades["source"]) == {"xml"}
    assert result.report.n_form4_listed == 1 and result.report.n_form4_fetched == 1
    assert result.seen == {}


def test_known_form_4s_are_not_fetched_again() -> None:
    ref, filing = form4("a1")
    provider = FakeFilings(refs={3453: [ref]}, filings={"a1": filing})

    result = run(provider, known={"a1"})

    assert provider.fetched == []
    assert result.trades.empty
    assert result.report.n_form4_listed == 1 and result.report.n_form4_fetched == 0


def test_derivative_only_filings_are_remembered_so_they_are_not_refetched() -> None:
    ref, filing = form4("a1", derivative_only=True)
    provider = FakeFilings(refs={3453: [ref]}, filings={"a1": filing})

    first = run(provider)
    second = run(provider, known=set(first.seen))

    assert first.trades.empty
    assert first.seen == {"a1": date(2026, 8, 28)}
    assert provider.fetched == ["a1"]  # only the first run fetched it
    assert second.seen == {}


def test_form_4s_before_the_gap_window_are_ignored() -> None:
    ref, filing = form4("a1", filed=date(2026, 7, 1))
    provider = FakeFilings(refs={3453: [ref]}, filings={"a1": filing})

    result = run(provider)

    assert provider.fetched == []
    assert result.report.n_form4_listed == 0


def test_8ks_in_the_events_window_become_events() -> None:
    inside = make_ref("k1", "8-K", date(2026, 8, 20), items=("2.02", "9.01"))
    outside = make_ref("k0", "8-K", date(2025, 6, 1), items=("8.01",))
    provider = FakeFilings(refs={3453: [inside, outside]})

    result = run(provider)

    assert list(result.events["accession"]) == ["k1"]
    assert result.events.iloc[0]["items"] == "2.02,9.01"
    assert result.events.iloc[0]["ticker"] == "ACME"
    assert result.report.n_events == 1
    assert provider.fetched == []  # 8-Ks need no document download


def test_failures_are_counted_and_the_permanent_ones_remembered() -> None:
    refs = [make_ref(a, "4", date(2026, 8, 28)) for a in ("a1", "a2", "a3")]
    _, good = form4("a3")
    provider = FakeFilings(
        refs={3453: refs},
        filings={"a3": good},
        failures={"a1": EdgarNotFound("gone"), "a2": EdgarFormatError("broken")},
    )

    result = run(provider)

    assert list(result.trades["accession"]) == ["a3"]
    assert result.report.n_form4_failed == 2
    assert result.report.n_form4_fetched == 1
    assert set(result.seen) == {"a2"}  # the malformed one is not retried, the missing one is


def test_blocking_stops_the_run() -> None:
    ref, _ = form4("a1")
    provider = FakeFilings(refs={3453: [ref]}, failures={"a1": EdgarBlocked("403")})

    with pytest.raises(EdgarBlocked):
        run(provider)


def test_a_company_without_a_filing_list_is_skipped() -> None:
    ref, filing = form4("a1")
    provider = FakeFilings(
        refs={3453: [ref]}, filings={"a1": filing}, list_failures={9: FilingNotFound("no CIK 9")}
    )

    result = run(provider, ciks={"BETA": 9, "ACME": 3453})

    assert result.report.n_companies == 2
    assert result.report.n_companies_missing == 1
    assert list(result.trades["accession"]) == ["a1"]


def test_an_empty_company_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="ciks"):
        run(FakeFilings(), ciks={})


def test_the_domain_errors_are_what_the_service_catches() -> None:
    assert issubclass(EdgarNotFound, FilingNotFound)
    assert issubclass(EdgarFormatError, FilingFormatError)


# ---- the gap window ----


def trades_filed(*days: date) -> pd.DataFrame:
    rows = [
        {
            "accession": f"a{i}",
            "issuer_cik": 1,
            "ticker": "A",
            "form": "4",
            "filing_date": day,
            "trade_date": day,
            "code": "S",
            "kind": "open_market_sell",
            "shares": 1.0,
            "price": 1.0,
            "acquired": "D",
            "direct": "D",
            "shares_after": 1.0,
            "owner_name": "",
            "owner_role": "",
            "plan": "no",
            "source": "bulk",
        }
        for i, day in enumerate(days)
    ]
    from whymoved.domain.insider import TRADE_COLUMNS

    return normalize_trades(pd.DataFrame(rows, columns=TRADE_COLUMNS))


def test_the_gap_starts_just_before_the_newest_stored_filing() -> None:
    trades = trades_filed(date(2026, 8, 1), date(2026, 9, 28))

    assert gap_start(trades, date(2026, 11, 2)) == date(2026, 9, 21)


def test_an_empty_table_looks_back_a_fixed_number_of_days() -> None:
    assert gap_start(empty_trades(), date(2026, 11, 2)) == date(2026, 7, 25)


def test_stale_data_cannot_cause_an_enormous_backfill() -> None:
    trades = trades_filed(date(2025, 1, 15))

    assert gap_start(trades, date(2026, 11, 2)) == date(2026, 4, 16)


# ---- merging events ----


def events(*rows: tuple[str, date, str]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "ticker": "ACME",
                "cik": 3453,
                "accession": accession,
                "form": "8-K",
                "filing_date": filed,
                "accepted_at": None,
                "items": items,
                "primary_document": "x.htm",
            }
            for accession, filed, items in rows
        ],
        columns=[
            "ticker",
            "cik",
            "accession",
            "form",
            "filing_date",
            "accepted_at",
            "items",
            "primary_document",
        ],
    )
    return normalize_events(frame)


def test_fresh_events_replace_stored_ones_and_everything_is_sorted() -> None:
    existing = events(("k2", date(2026, 8, 20), "8.01"), ("k1", date(2026, 6, 1), "2.02"))
    fresh = events(("k2", date(2026, 8, 20), "2.02,9.01"), ("k3", date(2026, 9, 1), "5.02"))

    merged = merge_events(existing, fresh)

    assert list(merged["accession"]) == ["k1", "k2", "k3"]
    assert merged.set_index("accession").loc["k2", "items"] == "2.02,9.01"


def test_events_before_the_cutoff_are_dropped() -> None:
    existing = events(("k1", date(2025, 1, 1), "2.02"), ("k2", date(2026, 8, 20), "2.02"))

    merged = merge_events(existing, events(), cutoff=date(2025, 11, 2))

    assert list(merged["accession"]) == ["k2"]


def test_merging_nothing_gives_an_empty_table() -> None:
    assert merge_events(events(), events()).empty
