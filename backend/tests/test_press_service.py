from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from whymoved.adapters.edgar_errors import EdgarFormatError, EdgarNotFound
from whymoved.adapters.press_store import ParquetPressStore, read_ticker
from whymoved.domain.corporate_events import EVENT_COLUMNS, normalize_events
from whymoved.domain.press import PressRelease, normalize_press, press_row
from whymoved.services.press import collect_press_releases, merge_press, pending_filings


def events(*rows: tuple[str, str, str, str, date]) -> pd.DataFrame:
    """Rows of (accession, ticker, form, items, filing date)."""
    return normalize_events(
        pd.DataFrame(
            [
                {
                    "ticker": ticker,
                    "cik": 7,
                    "accession": accession,
                    "form": form,
                    "filing_date": filed,
                    "accepted_at": None,
                    "items": items,
                    "primary_document": f"{accession}.htm",
                }
                for accession, ticker, form, items, filed in rows
            ],
            columns=EVENT_COLUMNS,
        )
    )


@dataclass
class FakePress:
    answers: dict[str, object] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def press_release(self, cik: int, accession: str) -> tuple[str, PressRelease | None] | None:
        self.calls.append(accession)
        answer = self.answers.get(accession)
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[return-value]


RELEASE = PressRelease(title="Acme Reports Results", summary="Acme today reported results.")


def test_pending_filings_are_those_with_exhibits_not_yet_checked_newest_first() -> None:
    stored = events(
        ("old", "ACME", "8-K", "2.02,9.01", date(2026, 1, 5)),
        ("new", "ACME", "8-K", "7.01,9.01", date(2026, 3, 5)),
        ("noex", "ACME", "8-K", "5.02", date(2026, 3, 6)),
        ("six", "ACME", "6-K", "", date(2026, 2, 1)),
        ("done", "ACME", "8-K", "9.01", date(2026, 3, 7)),
        ("tenq", "ACME", "10-Q", "", date(2026, 3, 8)),
    )

    pending = pending_filings(stored, known={"done"})

    assert list(pending["accession"]) == ["new", "six", "old"]


def test_collecting_records_releases_missing_exhibits_and_bad_filings() -> None:
    pending = events(
        ("a", "ACME", "8-K", "9.01", date(2026, 3, 5)),
        ("b", "ACME", "8-K", "9.01", date(2026, 3, 4)),
        ("c", "ACME", "8-K", "9.01", date(2026, 3, 3)),
        ("d", "ACME", "8-K", "9.01", date(2026, 3, 2)),
        ("e", "ACME", "8-K", "9.01", date(2026, 3, 1)),
    )
    provider = FakePress(
        answers={
            "a": ("https://sec.test/a.htm", RELEASE),
            "b": None,  # no exhibit 99
            "c": ("https://sec.test/c.htm", None),  # unreadable exhibit
            "d": EdgarNotFound("lagging index"),  # tried again next run
            "e": EdgarFormatError("broken"),
        }
    )

    fresh, report = collect_press_releases(provider, pending, max_filings=10)

    assert list(fresh["accession"]) == ["a", "b", "c", "e"]
    assert fresh.set_index("accession").loc["a", "title"] == "Acme Reports Results"
    assert fresh.set_index("accession").loc["b", "url"] == ""
    assert fresh.set_index("accession").loc["c", "title"] == ""
    assert (report.n_checked, report.n_found, report.n_failed) == (4, 1, 1)


def test_runs_stop_at_the_cap_and_the_deadline_and_save_in_batches() -> None:
    pending = events(*[(f"x{k}", "ACME", "8-K", "9.01", date(2026, 3, 1)) for k in range(7)])
    provider = FakePress()
    batches: list[int] = []

    _, capped = collect_press_releases(
        provider, pending, max_filings=5, on_batch=lambda b: batches.append(len(b)), batch_size=2
    )
    ticks = iter([0.0, 0.0, 0.0, 99.0])
    _, timed = collect_press_releases(
        provider, pending, max_filings=10, deadline=1.0, clock=lambda: next(ticks)
    )

    assert capped.n_checked == 5 and batches == [2, 4]
    assert timed.n_checked == 3
    with pytest.raises(ValueError, match="non-negative"):
        collect_press_releases(provider, pending, max_filings=-1)


def test_merging_keeps_only_filings_still_stored() -> None:
    old = normalize_press(
        pd.DataFrame([press_row("a", "ACME", "u", None), press_row("gone", "ACME", "u", None)])
    )
    fresh = normalize_press(pd.DataFrame([press_row("a", "ACME", "u2", RELEASE)]))

    merged = merge_press(old, fresh, keep={"a"})

    assert list(merged["accession"]) == ["a"] and merged.iloc[0]["url"] == "u2"


def test_the_store_reads_one_ticker_with_releases_only(tmp_path: Path) -> None:
    store = ParquetPressStore(tmp_path)
    rows = [press_row(f"a{k}", f"T{k % 30:02d}", "u", RELEASE) for k in range(3000)]
    rows.append(press_row("none", "T01", "", None))
    store.save(normalize_press(pd.DataFrame(rows)))

    one = read_ticker(store.path, "T01")

    assert len(store.load()) == 3001
    assert set(one["ticker"]) == {"T01"} and len(one) == 100  # the empty row is left out
    assert read_ticker(tmp_path / "missing.parquet", "T01").empty
