# 0006: Universe, data quality and job settings

Universe: holdings of an iShares Russell 3000 ETF snapshot, equities only, committed to the
repo with its date. Each stock gets a sector ETF through a small lookup table. Holdings with
no mapped sector are dropped and listed.

Quality filter: a stock must have data on at least 95 percent of calendar days, no long runs of
missing days, a median price above a floor, no more than a couple of enormous one-day moves
(possible split or data errors), and not too many days with an unchanged price (stale closes,
common for thinly traded stocks on a partial feed). All limits are in one config object and
every failure is reported with its reason, so the data decides the cutoff. The defaults are
starting points and will be tuned on real data in M2.

Duplicates: stocks whose returns correlate at 0.98 or more with a higher-priority stock are
dropped (share classes, clones). Without this, GOOG and GOOGL would be each other's top peer.
Priority is trading volume, so the more liquid class survives.

Job settings: the nightly job requires its API keys and an SEC contact string, with no
defaults, and fails at startup if one is missing. Secrets are SecretStr, so they do not
appear in logs. The API process uses lenient settings so it can start without them.

Assumptions to check against the real file: the holdings CSV layout, the sector names, and
the class-share ticker aliases.

Update (November 2026): coverage and gaps are now measured from each stock's first trade, with
a minimum of 260 trading days of history (the factor model needs 252 earlier days to explain a
day). The original rule counted the days before a listing as missing data, so every stock that
listed after the window began failed, including PLTR, ABNB, SNOW, COIN, RIVN and DASH. On the
real data the clean universe grows from 1,972 to 2,425 stocks; a stock that stops trading still
fails, because days after its last trade still count.
