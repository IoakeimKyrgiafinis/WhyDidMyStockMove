# 0011: Insider trades from the SEC's bulk data sets

Why bulk: fetching every Form 4 as its own XML file for a seven-year study would take hundreds of
thousands of requests (rough estimate). The SEC publishes quarterly ZIPs of flattened tables, about
8 to 16 MB each, from 2006. Seven years is about 28 downloads.

Verified against the SEC's readme and download page: column names (DOCUMENT_TYPE, ISSUERCIK,
TRANS_CODE, ...), DD-MON-YYYY dates, the transaction code list, and the AFF10B5ONE checkbox, which
exists only in 2023 and later files. Newer quarters are hosted in a different directory from older
ones, so the downloader tries both.

Not yet verified: the file names inside the ZIPs and the exact RPTOWNER_RELATIONSHIP format. The
reader is tolerant of several spellings and the smoke script checks real files.

Refreshing: late-quarter filings roll into the next release, and the newest file can be posted
before its quarter is complete, so the latest two quarters are re-downloaded on every run. Rows are
replaced by accession number, which makes re-imports safe. A small state file remembers which
quarters were imported.

Scope of the table: non-derivative transactions from Form 4 only, for issuers in the clean
universe (matched by CIK, because filers' own symbols change). Amendments (4/A) are excluded, since
they would double count trades; a trade later corrected by an amendment keeps its as-filed values.
Forms 3 and 5 are excluded. The ticker comes from the universe, not from the filing.

Owner: a filing can have several reporting owners. The transaction is attributed to the most
senior (officer, then director, then 10% owner, then other).

Plan label: the checkbox decides when present; otherwise a footnote that mentions a Rule 10b5-1
plan counts as a plan; otherwise the label is "unknown". Older filings are therefore mostly
"unknown" unless a footnote says otherwise.

Priority: when the same filing exists from bulk data and from EDGAR XML (M2.7), the bulk copy wins.

Limit: until the XML gap fill exists, the newest filings are missing for up to a quarter.
