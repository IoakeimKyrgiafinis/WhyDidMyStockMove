# 0010: News collection

Source: Alpaca's news endpoint (Benzinga), tagged to tickers, history back to 2015. Whether it is
available on the free plan is unconfirmed until the smoke script has run with a real key.

Stored: headline, source, link, timestamps and tickers only. Article text and summaries are not
kept: licences usually do not allow republishing them, and the site links to the original. The
licence terms have not been read and must be checked before headlines are shown publicly.

Incremental runs: fetch from the newest stored headline minus a 2-day overlap, deduplicate by
article id, and keep the later version when an article was updated. The overlap exists because
the documentation does not say whether start and end filter on creation or update time, and
because articles can be re-tagged after publication.

Window: only the last 2 years are kept (configurable), which keeps the published file small. A
force-full run refetches everything. Volume and file size are estimates until a real run.

Batching: 50 symbols per request, each paginated (limit 50 per page) with a page cap and a
repeated-token guard. An article tagged to symbols in several batches is returned once.

Tolerance: unlike prices, a malformed article is skipped and counted instead of failing the run,
because one odd item from a third-party feed should not stop a nightly job.

Retries: a small AlpacaHttp helper provides the retry and rate-limit behaviour. The price
adapter still has its own copy of that logic; folding it onto AlpacaHttp is deferred until the
price adapter's tests have been seen passing.
