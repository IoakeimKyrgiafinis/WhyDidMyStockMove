# 0007: Alpaca price adapter

Interface: the nightly job depends on a PriceProvider port (daily_bars for a list of symbols
and a date range). The Alpaca adapter is one implementation; tests and later steps use fakes.
The domain and services never import HTTP code.

Behaviour: symbols are fetched in batches of 100, each paginated until Alpaca returns no page
token, with guards against a repeated token or an unreasonable page count. Adjustment is "all"
(splits and dividends), because daily returns should not show ex-dividend drops as losses.
The feed (iex or sip) comes from settings and is always sent explicitly.

Retries: HTTP 429, 5xx and network errors are retried with exponential backoff (1s, 2s, 4s,
capped at 30s, 5 attempts). A 429 honours Retry-After, then X-RateLimit-Reset. If a successful
response reports no quota left, the adapter waits for the reset before the next call. There is
no jitter: one nightly client does not need it. Authentication and other 4xx errors fail
immediately with Alpaca's own message, so a plan restriction shows up as a clear error.

Parsing is faithful: the adapter returns what Alpaca sent, including odd values. Cleaning and
rejection belong to the quality filter. Requested symbols with no data come back as all-NaN
columns, so they are reported instead of silently disappearing.

Testing: everything is tested against a mocked HTTP transport. Rate-limit header behaviour and
the free plan's feed restrictions are known only from documentation until the smoke script has
run against the real API.

Estimate (unverified): about 4.4 million daily bars for a 2,500-stock universe over 7 years is
roughly 465 requests, a few minutes at 200 requests a minute.
