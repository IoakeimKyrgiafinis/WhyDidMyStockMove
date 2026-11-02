# 0014: Intraday reactions to headlines

Decision: score each headline with the daily factor model run on 5-minute bars, fitted only on
the 40 sessions before the headline's session (at least 10 required).

Data: Alpaca 5-minute bars on the IEX feed, fetched on demand for the stock, SPY and the
stock's sector ETF over the previous 70 calendar days, cached per stock and session. SPY is the
intraday market factor rather than IWV, which trades too thinly on IEX for clean 5-minute
returns. Sessions where the stock traded in fewer than 80% of the bars are dropped; single
missing bars are filled with the last close. The first bar of each session is excluded from the
intraday returns because it would span the overnight gap.

Time of day: residual volatility is estimated per 30-minute bucket, because the open and close
are much noisier than midday. A reaction's z-score uses the volatility of the bars it covers.

Window: 30 minutes from the bar that contains the headline (the reaction to news can start
within seconds, so that bar counts as "after"). The same 30 minutes before the headline are
reported beside it, and flagged when the stock had already moved by 1.96 sigma: many headlines
are written after the move. Headlines within 15 minutes of each other share one result.

Significance: an empirical p-value from the same time-of-day window on each earlier session
(their residuals are in-sample, which makes the reference slightly calm and the test slightly
generous). The page states how many flags chance alone would produce.

Outside market hours: the move from the previous close to the first five-minute bar of the next
session, against what the market and sector did over the same gap, scored by the standard
deviation of earlier overnight residuals. All such headlines share one result.

Configuration: the API needs WHYMOVED_ALPACA_KEY_ID and WHYMOVED_ALPACA_SECRET_KEY. Without them
the rest of the site works and the chips say they are unavailable.

Limits: thin stocks have sparse IEX bars and often cannot be scored; the first five-minute bar is
an approximation of the opening price; timing shows coincidence, not cause.
