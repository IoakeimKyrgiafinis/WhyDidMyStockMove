# 0018: World press coverage from GDELT

Why not the search API: GDELT's DOC search API asks for at most one request every five seconds
and, in November 2026, answered HTTP 429 to every request from two different networks, waits of
a minute included. A feature that calls it when a visitor opens a day would rarely work. The raw
Global Knowledge Graph files have no key and no limit: one zipped table every 15 minutes, about
a thousand articles each, with the page title, the organisations the article names and its
tone. The nightly job reads the files since its last run (a day is 96 files, under a minute to
read); the first run goes back 30 days, and a longer history can be filled in with
`--backfill-days`.

Matching an article to a company: the title must name the company and GDELT must have found the
company among the article's organisations ("Apple" in a recipe headline is not Apple), or the
title carries the company's exchange tag or cashtag ("(NASDAQ:NVDA)", "$NVDA"). Company names
are the listing names without suffixes ("NVIDIA CORP" -> "nvidia"); names shorter than three
letters match only through tags. "Nasdaq" is never a mention of Nasdaq Inc., since every
"(NASDAQ:X)" tag would count. A list of automated sites that publish a story per filing and price
move is dropped, as are giveaways.

On 8 hours of real files, matched articles were about the company in nearly every case; the
noise left is product and entertainment coverage (Netflix's shows, Walmart's shelves) and other
firms sharing a name. So:

- Copies of one story (syndicated, or with the site's name added) are folded into one, with the
  number of outlets that ran it.
- Stories whose headline is about the business (shares, earnings, deals, analysts, lawsuits,
  executives) come first, then the most widely carried.
- The count of articles is compared with the median of the stock's other trading days, once 20
  days of coverage exist.
- Nothing from GDELT feeds a statistic.

Timing: an article counts from when GDELT saw it, which is never before it was published, and is
placed like a headline (after the 16:00 close it counts toward the next session).

Memory: the articles stay on disk, sorted by ticker. The API reads one stock's article times to
count coverage per day (kept per stock) and the titles of one week when a day is opened.
Articles older than 400 days are dropped.
