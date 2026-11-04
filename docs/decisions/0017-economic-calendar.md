# 0017: The economic calendar

What: Fed rate decisions, CPI and core CPI, PPI, PCE and core PCE prices, the jobs report
(payrolls and unemployment), weekly jobless claims, retail sales, GDP growth and job openings
(JOLTS). These are the scheduled releases that move the whole market.

Source: FRED, the St. Louis Fed's free API (a key is required). Each figure is read with FRED's
"initial release only" view, which gives every observation as first published together with the
date it came out. That date is the release day, and the figure is what traders saw that
morning, not the number after later revisions. The comparison is the first print of the period
before, for the same reason. Fed decisions come from the dates of FRED's "FOMC Press Release"
and the upper end of the target range on the day before and after (a decision takes effect the
next day; the range has been a quarter point wide since 2008).

Timing: every one of these comes out at a fixed time before the close (8:30, 10:00 or 2:00 PM
New York time), so it moves the session of its own date; a release on a market holiday counts
toward the next session.

Use: the releases are shown in the news view with the market part of the day's move. They are
market-wide, so they explain the market factor, not the stock's own unexplained move; they are
not used in any statistic.

Collection: about a dozen requests a night, re-reading the whole window so changed release
dates are picked up. A series that fails keeps last night's rows; the job fails only when
nothing could be read, and the site works without the file.
