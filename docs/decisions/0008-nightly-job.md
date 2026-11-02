# 0008: Nightly job

Flow: parse the holdings snapshot, attach sector ETFs, update prices, assess quality, drop
share-class clones, and write a quality report and a universe summary.

Incremental updates: stocks with stored data are fetched from 10 days before the last stored
date. Prices are adjusted for splits and dividends, so a new ex-date changes a stock's whole
history. If the re-fetched overlap differs from the stored prices (relative difference above
1e-6), that stock is re-fetched in full. Dividend-paying stocks therefore refetch a few times a
year, which is cheap. A weekly --force-full run is a safety net.

Partial bars: the end date defaults to yesterday (UTC), so a session still in progress is never
stored.

Calendar: trading days are inferred from the data (a day counts when at least half of the
symbols with any data have a close), which avoids a calendar dependency. A day on which most
symbols lack data is dropped, which would make that day's return a multi-day return.

Strictness: the market and sector ETFs must pass the quality checks or the job stops. Individual
stocks that fail are reported with their reasons and left out. Priority among near-duplicates
is median dollar volume over the last 60 days; on a partial feed this ranks classes, not
absolute volume.

Storage: two parquet files (closes, volumes) written to temporary names and then renamed, so a
failed write does not damage the existing data. Open question: where these files live in
production. A scheduled job and the API need to share them, and some free hosts have disks that
do not persist.

Testing: the job runs against a fake provider and an in-memory store. The real API and the real
files are checked by the smoke script and by a manual first run.
