# 0013: Timeline placement and the insider studies

Placement: every headline, 8-K and Form 4 is put on the first regular session that could react
to it. Before the 16:00 New York close it is that day; at or after the close, the next day;
weekends and holidays snap forward to the next trading day (at most 5 calendar days).

- Headlines carry true UTC timestamps and are converted to New York time (with daylight saving).
- EDGAR acceptance times are labelled UTC but widely reported to be Eastern wall-clock times.
  The stamped time is read as Eastern. A stamp before 16:00 was public before the close under
  either reading, so this never builds in look-ahead; at worst it gives up a filing day.
- The bulk Form 4 data sets have no acceptance time, so insider filings count from the day
  after the filing date. This gives up the filing day's reaction rather than risk look-ahead.

Per-stock insider study: abnormal returns (the out-of-sample unexplained move) around open-market
purchases, window -5 to +20 trading days, from the day the filing became public. Overlapping
purchases count once. CARs for day 0 to +1, the first week and the first month are tested with
a cross-sectional t-test (Student-t intervals) and BMP. A single stock has few purchases, so the
permutation test uses the first week, where power is reasonable; on synthetic data with a planted
2.5% first-week drift and 12 events, a 20-day permutation test missed it (p about 0.11).

Pooled study: a nightly batch job runs the same windows across the whole universe for purchases,
discretionary sales and 10b5-1 sales. Events on the same date share market shocks, so they are
averaged per date first and the t-test runs across dates. The result is published as
insider_study.json with the snapshot.

Earnings reaction: on 8-K Item 2.02 days, the mean absolute unusual-move score is compared with
random sets of the same number of scored days (one-sided permutation test). Size, not direction:
good and bad news cancel out in an average.

Memory: the API keeps the trades table with its repeated text columns as categories, and the
optional files (news, 8-Ks, trades, study) are loaded only if present and readable, so a failed
collector never takes the site down.

Limits: headline timing shows coincidence, not cause; free headline feeds are often written after
the move. Per-stock insider results are usually inconclusive, and the page says so and points to
the pooled study.
