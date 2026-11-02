from __future__ import annotations

from datetime import date, datetime

from whymoved.domain.timing import session_for_eastern, session_for_filing, session_for_utc


def test_before_the_close_is_the_same_session() -> None:
    assert session_for_eastern(datetime(2026, 10, 29, 8, 0)) == date(2026, 10, 29)
    assert session_for_eastern(datetime(2026, 10, 29, 15, 59)) == date(2026, 10, 29)


def test_at_or_after_the_close_is_the_next_session() -> None:
    assert session_for_eastern(datetime(2026, 10, 29, 16, 0)) == date(2026, 10, 30)
    assert session_for_eastern(datetime(2026, 10, 29, 23, 0)) == date(2026, 10, 30)


def test_utc_headlines_are_converted_to_new_york_time() -> None:
    # 19:59 UTC is 15:59 in New York in summer (EDT, UTC-4) ...
    assert session_for_utc(datetime(2026, 7, 1, 19, 59)) == date(2026, 7, 1)
    # ... but 20:30 UTC is 16:30, after the close.
    assert session_for_utc(datetime(2026, 7, 1, 20, 30)) == date(2026, 7, 2)
    # In winter (EST, UTC-5) 20:30 UTC is 15:30, still before the close.
    assert session_for_utc(datetime(2026, 1, 15, 20, 30)) == date(2026, 1, 15)
    # 02:00 UTC is the previous evening in New York, so the next morning's session.
    assert session_for_utc(datetime(2026, 1, 16, 2, 0)) == date(2026, 1, 16)


def test_filings_use_the_acceptance_time() -> None:
    assert session_for_filing(date(2026, 8, 3), datetime(2026, 8, 3, 9, 15)) == date(2026, 8, 3)
    assert session_for_filing(date(2026, 8, 3), datetime(2026, 8, 3, 17, 30)) == date(2026, 8, 4)


def test_filings_without_an_acceptance_time_count_from_the_next_day() -> None:
    assert session_for_filing(date(2026, 8, 3), None) == date(2026, 8, 4)
