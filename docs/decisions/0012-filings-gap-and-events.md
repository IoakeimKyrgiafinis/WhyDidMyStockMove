# 0012: Gap fill, 8-K events and the nightly workflow

One pass: a company's submissions list contains both its Form 4s and 8-Ks, so one request per
company serves the insider gap fill and the events. Older pages are fetched only when a start date
reaches beyond the latest ~1,000 filings.

Gap window: starts 7 days before the newest stored filing, capped at 200 days back, and defaults to
100 days with no stored data. The cap stops a stale table from causing an enormous backfill.

Not refetching: filings already in the trades table are skipped. A Form 4 with only derivative
transactions produces no rows and would be refetched every night, so those accessions are kept in
a small "seen" file, pruned as the window moves. A malformed filing is remembered too; a missing one
may be an index lag and is retried.

Priority: rows from the bulk data sets always replace rows from XML for the same filing, so each
quarterly release cleans up the gap-filled data. XML rows exist only for filings newer than the
latest release.

Events: Form 8-K only (amendments would duplicate), with item numbers, acceptance time (for the
event study's tradable-date rule) and a lookup of common item labels. Kept for 2 years by default.
The item labels are from memory of the SEC's item list.

Errors: the service catches domain errors (FilingNotFound, FilingFormatError), which the EDGAR
adapter's errors inherit from, so the service does not import adapter code. Blocking (HTTP 403) and
exhausted retries are not caught and stop the run.

Workflow: restore the previous snapshot from a GitHub Release, run the four jobs, upload the new
snapshot. Each collector other than prices is allowed to fail without blocking publication, and the
run is then marked failed so a partial snapshot is not silent. Replacing release assets is not
atomic. Scheduled workflows in public repositories are disabled after a period of inactivity (about
60 days, from memory), so the schedule needs an occasional commit or manual run.
