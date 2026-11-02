# 0004: Event study

Abnormal return: a day's unexplained move from the factor model (M1.1), which uses betas
fitted only on earlier days, so the measurement is out of sample. Volatility forecasts
come from the same EWMA as the unusual-move score (M1.2).

Statistics: cumulative abnormal return over a window, tested with a cross-sectional
t-test and the BMP standardised test. Both use the spread across events, so they stay
valid when event days are more volatile than ordinary days.

Permutation test: compares the real events with the same number of random trading days
that satisfy the same window rules. It makes no distributional assumption. Random dates
may overlap each other while the real events do not, which if anything makes the test a
little conservative.

Rules: events map forward to the next trading day; duplicates count once; windows with
missing data are dropped; overlapping events are dropped by default, because
overlapping windows are not independent. Every drop is counted by reason.

Known limits: the tests treat events as independent. Events that share a calendar date
(market-wide announcements, many insiders filing together) violate that, which the
pooled study in M4 will address by clustering on date. An event filed after the close
should map to the next trading day; M4 will use EDGAR acceptance timestamps for this.
Cumulated daily abnormal returns are arithmetic and suit short windows only.
