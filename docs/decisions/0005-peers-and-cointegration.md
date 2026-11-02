# 0005: Peer ranking, validation and cointegration

Ranking: stocks are ranked by correlation of residuals, after removing the market and the
stock's own pure sector factor. Raw correlation between two stocks in a sector mostly
reflects that they share the market and the sector, so it ranks same-sector stocks above
genuinely linked stocks in other sectors. Raw ranking stays available as a layer.

Out of sample: a Residualizer is fitted on the selection window and then applied unchanged
to the validation window, so validation residuals use no information from that window.
Peers are chosen on the selection window; the page shows each peer's correlation on both
windows. Choosing the best few of thousands inflates correlations (the winner's curse), and
the drop on the later window is what that looks like.

Intervals: Fisher z intervals assume independent, roughly normal observations. Daily
returns have fat tails and clustered volatility, so they are somewhat too narrow. They are
shown as a guide, and a bootstrap interval may replace them after M2.

Grid order: average-linkage clustering on sqrt((1 - r) / 2) with optimal leaf ordering, so
related stocks sit together.

Cointegration: Engle-Granger on log prices, with the selected stock as the dependent
series, using statsmodels coint (MacKinnon critical values) and not a plain ADF test on
regression residuals. It runs on the validation window, so the tests are conditional on a
selection made earlier. Benjamini-Hochberg controls false discoveries across the peers
tested; it does not cover the screening of the whole universe, which is why selection and
testing use different windows.

Known limits: cointegration describes price levels, not whether stocks move together day to
day, so it is a secondary badge. Results depend on which series is dependent, and we use one
fixed direction. Power is low on short windows. Correlations drift over time. Peers are
historical relationships, not predictions.
