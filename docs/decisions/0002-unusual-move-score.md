# 0002: Unusual-move score

Decision: standardise each day's unexplained move by an EWMA volatility forecast
(lambda 0.94) built only from earlier residuals, then report an empirical two-sided
tail probability against the previous 250 scores. Show the normal-distribution
probability beside it, and show the number of flags expected from chance.

Why EWMA: volatility clusters, so a fixed standard deviation would flag every day
of a turbulent month. Lambda 0.94 is the RiskMetrics daily convention. It is not
fitted per stock.

Why empirical, not normal: stock residuals have fat tails, so a bell curve makes
big moves look rarer than they are. The page shows both numbers.

Limits: flags are not adjusted for testing many days; the expected-false-alarm
count is shown instead. Scheduled events such as earnings produce predictable
outliers, which M4 will mark on the timeline. The empirical p-value assumes the
last 250 days are comparable to today.
