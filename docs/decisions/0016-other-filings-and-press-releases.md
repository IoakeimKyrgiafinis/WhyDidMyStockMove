# 0016: Other company filings and 8-K press releases

Forms: besides 8-Ks the filings job keeps 6-Ks, Schedules 13D and 13G (both the old "SC 13D"
and the 2025 "SCHEDULE 13D" names), registrations and offering prospectuses (S-1, F-1, S-3,
F-3, 424B1, 424B4, 424B5), tender offers (SC TO-T, SC TO-I, SC 14D9), merger proxies (DEFM14A)
and periodic reports (10-Q, 10-K, 20-F, 40-F). They come from the same filing list the job
already downloads, so they cost no extra requests. 424B2, 424B3 and 424B8 are left out: banks
file tens of thousands of them for structured notes (JPMorgan: 23,000 in two years).

Holder filings: a company's filing list also holds the 13Ds and 13Gs it files as a holder of
other companies' shares. The first ten digits of an accession number are the submitter's CIK, so
a schedule submitted under the company's own CIK is dropped (JPMorgan: 326 of 341). Schedules
sent through a filing agent cannot be told apart this way; offerings and stakes are therefore
shown once per form and day, which keeps an agent's batch to one row.

Routine filings (13G, shelf registrations, periodic reports) are listed but dimmed and get no
chart marker.

Press releases: an 8-K with Item 9.01, and every 6-K, may attach the release as exhibit 99. The
press job reads the filing's index page, finds the first HTML or text exhibit 99, and keeps its
headline and lead paragraph. Two requests per filing, so runs are capped (10,000 filings or 60
minutes) and go newest first; the two-year history fills in over about four nights. Filings with
no exhibit, or an unreadable one, are stored empty so they are not fetched again.

Parsing is rule-based: text is split at block-level tags, letterhead, contact boxes, addresses,
dates and legal boilerplate are dropped, the first line of four or more words is the headline
(joined with the following lines when it ends in a connecting word), and the first paragraph of
140 or more characters that is not legal text or a bullet is the lead. On 85 real exhibits it
got about nine in ten right; investor presentations and financial statements attached as
exhibit 99 give poorer results, and the page links to the exhibit so the reader can check.

Memory: the releases stay on disk in a file sorted by ticker in row groups of 1,000; the API
reads one stock's groups when asked. The events table now loads with categories and Arrow
strings: 4 MB instead of 18 MB for the same 55,000 filings.
