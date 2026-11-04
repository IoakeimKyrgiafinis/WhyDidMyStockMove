# 0017: The economic calendar

What: Fed rate decisions, CPI and core CPI, PPI, PCE and core PCE prices, the jobs report
(payrolls and unemployment), weekly jobless claims, retail sales, GDP growth and job openings
(JOLTS). These are the scheduled releases that move the whole market.

Source: FRED, the St. Louis Fed's free API (a key is required). Every figure is the one first
published, what traders saw that morning, not the number after later revisions. The comparison
is the first print of the period before, for the same reason.

- Levels (unemployment, claims, GDP growth, JOLTS) are read with FRED's "initial release only"
  view, which gives every observation as first published together with the date it came out.
  That date is the release day.
- Changes (CPI, PCE, PPI, retail sales, payrolls) cannot be read that way: the view serves
  levels only. They are computed from the series as FRED had it on each day it was published
  (its vintages): a day that adds a new month is a release, and its figure is that month
  against the month before as published the same day. That is the number the release reported:
  payrolls of 158,858k for July against a revised 158,881k for June was the "−23k" of
  7 August 2026, not a change against June's first print. Days that only revise are skipped.
- Fed decisions use the meeting dates from the Fed's published calendar (kept in the code and
  extended each year when the next schedule comes out), plus the day before any change in the
  target that no meeting explains, which catches moves between meetings. FRED has no meeting
  list: its "FOMC Press Release" is dated every day. The figure is the upper end of the target
  range on the day of the decision and the day after (it takes effect the next day; the range
  has been a quarter point wide since 2008).

Timing: every one of these comes out at a fixed time before the close (8:30, 10:00 or 2:00 PM
New York time), so it moves the session of its own date; a release on a market holiday counts
toward the next session.

Use: the releases are shown in the news view with the market part of the day's move. They are
market-wide, so they explain the market factor, not the stock's own unexplained move; they are
not used in any statistic.

Collection: about a dozen requests a night, re-reading the whole window so changed release
dates are picked up. A series that fails keeps last night's rows; the job fails only when
nothing could be read, and the site works without the file.
