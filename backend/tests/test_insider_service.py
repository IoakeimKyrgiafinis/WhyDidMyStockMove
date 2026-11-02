from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
import pytest

from whymoved.domain.insider import TRADE_COLUMNS, empty_trades, normalize_trades
from whymoved.services.insider import import_quarters, merge_trades

DEFAULTS: dict[str, object] = {
    "accession": "a1",
    "issuer_cik": 3453,
    "ticker": "ACME",
    "form": "4",
    "filing_date": date(2026, 8, 28),
    "trade_date": date(2026, 8, 27),
    "code": "S",
    "kind": "open_market_sell",
    "shares": 100.0,
    "price": 10.0,
    "acquired": "D",
    "direct": "D",
    "shares_after": 500.0,
    "owner_name": "Doe Jane",
    "owner_role": "CEO",
    "plan": "no",
    "source": "bulk",
}


def make_trades(rows: list[dict[str, object]]) -> pd.DataFrame:
    return normalize_trades(
        pd.DataFrame([{**DEFAULTS, **row} for row in rows], columns=TRADE_COLUMNS)
    )


def pairs(frame: pd.DataFrame) -> list[tuple[str, str, float]]:
    return [
        (str(r["accession"]), str(r["source"]), float(r["shares"]))
        for r in frame.to_dict("records")
    ]


# ---- merging ----


def test_fresh_bulk_rows_replace_the_rows_of_the_same_filing() -> None:
    existing = make_trades(
        [
            {"accession": "a1", "shares": 100.0},
            {"accession": "a1", "shares": 200.0},
            {"accession": "a2"},
        ]
    )
    fresh = make_trades([{"accession": "a1", "shares": 999.0}])

    merged = merge_trades(existing, fresh)

    assert pairs(merged) == [("a1", "bulk", 999.0), ("a2", "bulk", 100.0)]


def test_xml_rows_never_override_bulk_rows() -> None:
    existing = make_trades([{"accession": "a1", "shares": 100.0}])
    fresh = make_trades([{"accession": "a1", "source": "xml", "shares": 111.0}])

    assert pairs(merge_trades(existing, fresh)) == [("a1", "bulk", 100.0)]


def test_bulk_rows_replace_xml_rows() -> None:
    existing = make_trades([{"accession": "a1", "source": "xml", "shares": 1.0}])
    fresh = make_trades([{"accession": "a1", "shares": 2.0}])

    assert pairs(merge_trades(existing, fresh)) == [("a1", "bulk", 2.0)]


def test_a_refetched_xml_filing_replaces_the_older_copy() -> None:
    existing = make_trades([{"accession": "a1", "source": "xml", "shares": 1.0}])
    fresh = make_trades([{"accession": "a1", "source": "xml", "shares": 2.0}])

    assert pairs(merge_trades(existing, fresh)) == [("a1", "xml", 2.0)]


def test_nothing_fresh_leaves_the_table_unchanged() -> None:
    existing = make_trades([{"accession": "a1"}])

    pd.testing.assert_frame_equal(merge_trades(existing, empty_trades()), existing)


def test_the_result_is_sorted_by_filing_date() -> None:
    existing = make_trades([{"accession": "a2", "filing_date": date(2026, 9, 5)}])
    fresh = make_trades([{"accession": "a1", "filing_date": date(2026, 8, 1)}])

    merged = merge_trades(existing, fresh)

    assert list(merged["accession"]) == ["a1", "a2"]


# ---- importing quarters ----


@dataclass
class FakeSource:
    frames: dict[tuple[int, int], pd.DataFrame | None]
    calls: list[tuple[int, int]] = field(default_factory=list)

    def trades(self, year: int, quarter: int) -> pd.DataFrame | None:
        self.calls.append((year, quarter))
        return self.frames.get((year, quarter))


def test_available_quarters_are_imported_and_missing_ones_reported() -> None:
    source = FakeSource(
        {
            (2026, 2): make_trades([{"accession": "a1"}]),
            (2026, 3): None,
            (2026, 4): make_trades([{"accession": "a2"}]),
        }
    )

    merged, report = import_quarters(source, empty_trades(), [(2026, 2), (2026, 3), (2026, 4)])

    assert source.calls == [(2026, 2), (2026, 3), (2026, 4)]
    assert report.imported == ("2026q2", "2026q4")
    assert report.unavailable == ("2026q3",)
    assert report.n_total == 2
    assert list(merged["accession"]) == ["a1", "a2"]


def test_importing_the_same_quarter_twice_does_not_duplicate_rows() -> None:
    frame = make_trades([{"accession": "a1"}, {"accession": "a1"}])
    source = FakeSource({(2026, 2): frame})

    first, _ = import_quarters(source, empty_trades(), [(2026, 2)])
    second, report = import_quarters(source, first, [(2026, 2)])

    assert len(first) == len(second) == 2
    assert report.n_total == 2


def test_progress_is_reported_after_each_quarter_so_a_failure_keeps_earlier_ones() -> None:
    class Source:
        def trades(self, year: int, quarter: int) -> pd.DataFrame | None:
            if quarter == 3:
                raise ValueError("Out of bounds nanosecond timestamp")
            return make_trades([{"accession": f"{year}-{quarter}"}])

    saved: list[tuple[int, str]] = []
    with pytest.raises(ValueError):
        import_quarters(
            Source(),
            empty_trades(),
            [(2026, 1), (2026, 2), (2026, 3)],
            lambda merged, key: saved.append((len(merged), key)),
        )

    assert saved == [(1, "2026q1"), (2, "2026q2")]
