# 0003: Variance split and model health

Variance split: shares of a stock's return variance from the market, the pure
sector factor, and the stock itself, over the selected window. Additive to 100
percent because the sector factor is orthogonalised against the market. It is a
description of the window, not a forecast, and unlike the daily decomposition it
is fitted in-sample.

Uncertainty: moving-block bootstrap (block of 10 days, 1000 resamples, percentile
interval). Returns show volatility clustering, so a plain bootstrap would give
intervals that are too narrow. Block length is a judgement call and will be
checked for sensitivity on real data.

Model health: R-squared, beta stability between the two halves of the window
(Newey-West z-statistics) and Ljung-Box autocorrelation set the badge. Jarque-Bera
and ARCH-LM are shown as information only, because daily stock data almost always
rejects both and counting them would flag every stock.

Thresholds are display heuristics, not statistical tests. They will be recalibrated
on the real universe in M2. Diagnostics are implemented directly and checked against
statsmodels in the tests, so the maths is verifiable and does not depend on library
typing.
