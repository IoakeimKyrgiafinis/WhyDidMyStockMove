"""When could an announcement first move the price?

An event study must not credit a day's return to news that arrived after that day's close,
or it builds in look-ahead. These helpers map a timestamp to the first regular session that
could have reacted to it. Weekends and holidays are handled later, when the event study
snaps a date forward to the next trading day.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)


def session_for_eastern(moment: datetime) -> date:
    """The session that can react to something published at `moment` (Eastern wall clock).

    Before the 16:00 close it is that day's session (pre-market news moves the open); at or
    after the close it is the next day's.
    """
    if moment.time() >= MARKET_CLOSE:
        return moment.date() + timedelta(days=1)
    return moment.date()


def session_for_utc(moment: datetime) -> date:
    """Like `session_for_eastern`, for a naive UTC timestamp (headlines)."""
    eastern = pd.Timestamp(moment).tz_localize("UTC").tz_convert(MARKET_TZ)
    return session_for_eastern(eastern.to_pydatetime().replace(tzinfo=None))


def session_for_filing(filing_date: date, accepted_at: datetime | None) -> date:
    """The first session that can react to an EDGAR filing.

    `accepted_at` is the EDGAR acceptance time as naive UTC. It is genuine UTC: across 55,000
    filings the stamps peak at 20:00-21:00 (just after the 16:00 New York close, when earnings
    come out) and 10:00-12:00 (before the open), and almost none fall between 03:00 and 10:00,
    when EDGAR is closed. Without an acceptance time the filing may have arrived after the
    close, so the next day is used, which gives up the filing day's reaction rather than risk
    look-ahead.
    """
    if accepted_at is None or pd.isna(accepted_at):
        return filing_date + timedelta(days=1)
    return session_for_utc(accepted_at)


def sessions_for_utc(moments: pd.Series) -> pd.Series:
    """`session_for_utc` for a whole column of naive UTC timestamps at once."""
    eastern = pd.DatetimeIndex(moments).tz_localize("UTC").tz_convert(MARKET_TZ)
    after_close = eastern.hour >= MARKET_CLOSE.hour
    days = eastern.tz_localize(None).normalize() + pd.to_timedelta(after_close.astype(int), "D")
    return pd.Series(days, index=moments.index, dtype="datetime64[ns]")
