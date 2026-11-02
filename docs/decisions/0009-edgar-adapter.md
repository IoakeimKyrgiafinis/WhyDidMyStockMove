# 0009: EDGAR adapter, Form 4 parsing

Interface: services depend on a FilingsProvider port (ticker map, filing lists, parsed Form 4).
The EDGAR client implements it and tests use fakes.

Access: the SEC requires a User-Agent with contact details and allows 10 requests a second. The
client sends the configured contact string, spaces requests about 0.12 seconds apart, and retries
429, 5xx and network failures with backoff. A 403 is reported at once with a hint, because the
likely causes (bad User-Agent, rate block) are not fixed by an immediate retry. A shared
RateLimiter and RetryPolicy live in http_policy.py and are also used by the Alpaca adapter.

Filing lists: the submissions response is columnar (parallel arrays) and holds roughly the
latest 1,000 filings. Older pages are fetched only when the requested start date reaches
beyond them. The first real run decides whether the older-page layout assumption holds.

Form 4: the raw XML is fetched, and an 'xsl...' folder prefix on the primary document is
removed, because EDGAR lists the rendered view there. Parsing uses defusedxml, so entity
expansion and external references are refused. The parser is tolerant of missing optional
fields (prices can be a footnote reference with no value) and strict about structure.

Classification: only open-market purchases (P) and sales (S) of the stock itself count as buys
and sells. Awards, option exercises, tax withholding, gifts and similar codes are labelled
routine, because they say little about what an insider thinks of the stock. The code meanings
are my reading and need checking against the SEC's code list.

10b5-1: the checkbox (aff10b5One, written as 1, 0, true or false) decides when present. Older
filings have no checkbox, so a footnote mentioning a plan is used instead.

Lag: business days from the trade to the filing, ignoring market holidays, so a holiday can
make a timely filing look one day late. The effective tradable date of a filing (acceptance
time, after-close filings, weekends) is handled in the event-study milestone, which needs a
time-zone database.

Testing: everything runs against a mocked transport and documents shaped like real filings. The
smoke script is the only check against the real service.
