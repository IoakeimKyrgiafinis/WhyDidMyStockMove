from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from whymoved.domain.corporate_events import (
    EVENT_COLUMNS,
    EVENT_FORMS,
    empty_events,
    event_row,
    is_own_stake_report,
    normalize_events,
)
from whymoved.domain.filings import FilingFormatError, FilingNotFound
from whymoved.domain.insider import empty_trades, trades_from_filing
from whymoved.services.ports import FilingsProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilingsReport:
    n_companies: int
    n_companies_missing: int  # the SEC had no filing list for these
    n_form4_listed: int  # Form 4s in the gap window
    n_form4_fetched: int  # fetched and parsed this run
    n_form4_failed: int
    n_events: int  # company filings in the events window


@dataclass(frozen=True, eq=False)
class FilingsResult:
    trades: pd.DataFrame  # fresh rows from Form 4 XML
    events: pd.DataFrame  # company filings in the events window
    seen: dict[str, date]  # processed filings that need no rows (derivative-only or malformed)
    report: FilingsReport


def gap_start(
    trades: pd.DataFrame,
    today: date,
    *,
    lookback_days: int = 100,
    margin_days: int = 7,
    max_days: int = 200,
) -> date:
    """Where the gap fill starts: just before the newest stored filing.

    With no stored trades it looks back `lookback_days`. If the stored data is stale, the start is
    capped at `max_days` ago so one run cannot turn into an enormous backfill.
    """
    if trades.empty:
        return today - timedelta(days=lookback_days)
    latest = pd.Timestamp(trades["filing_date"].max()).date()
    return max(latest - timedelta(days=margin_days), today - timedelta(days=max_days))


def merge_events(
    existing: pd.DataFrame, fresh: pd.DataFrame, cutoff: date | None = None
) -> pd.DataFrame:
    """Combine stored and fresh events, replacing rows by filing. Events before `cutoff` are
    dropped. The result is sorted by filing date, then accession."""
    kept = existing[~existing["accession"].isin(set(fresh["accession"]))]
    parts = [part for part in (kept, fresh) if not part.empty]
    if not parts:
        return empty_events()
    merged = pd.concat(parts, ignore_index=True)
    if cutoff is not None:
        merged = merged[merged["filing_date"] >= pd.Timestamp(cutoff)]
    merged = merged.sort_values(["filing_date", "accession"], kind="mergesort")
    return normalize_events(merged.reset_index(drop=True))


def collect_filings(
    provider: FilingsProvider,
    ciks: Mapping[str, int],
    known_accessions: Collection[str],
    *,
    form4_since: date,
    events_since: date,
) -> FilingsResult:
    """List each company's Form 4s and company filings (8-Ks and the other EVENT_FORMS), fetch
    the unseen Form 4s, and collect the company filings.

    One filing list per company serves both purposes. A missing company is counted and skipped;
    a missing or malformed filing is counted and skipped. Blocking or exhausted retries are not
    caught, so they stop the run.
    """
    if not ciks:
        raise ValueError("ciks must not be empty")
    earliest = min(form4_since, events_since)
    known = set(known_accessions)

    trade_parts: list[pd.DataFrame] = []
    event_rows: list[dict[str, object]] = []
    seen: dict[str, date] = {}
    missing = listed = fetched = failed = 0

    for ticker, cik in ciks.items():
        try:
            refs = provider.recent_filings(cik, {"4", *EVENT_FORMS}, earliest)
        except FilingNotFound:
            missing += 1
            logger.warning("no filing list for %s (CIK %d)", ticker, cik)
            continue

        for ref in refs:
            if ref.form in EVENT_FORMS:
                own_stake = is_own_stake_report(ref.form, ref.accession, cik)
                if ref.filing_date >= events_since and not own_stake:
                    event_rows.append(event_row(ticker, ref))
                continue
            if ref.form != "4" or ref.filing_date < form4_since:
                continue

            listed += 1
            if ref.accession in known:
                continue
            try:
                filing = provider.insider_filing(ref)
            except FilingNotFound as exc:  # may be a transient index lag: try again next run
                failed += 1
                logger.warning("skipping %s: %s", ref.accession, exc)
                continue
            except FilingFormatError as exc:  # permanent: remember it so it is not retried
                failed += 1
                seen[ref.accession] = ref.filing_date
                known.add(ref.accession)
                logger.warning("malformed filing %s: %s", ref.accession, exc)
                continue

            fetched += 1
            known.add(ref.accession)
            rows = trades_from_filing(filing, ticker)
            if rows.empty:
                seen[ref.accession] = ref.filing_date
            else:
                trade_parts.append(rows)

    trades = pd.concat(trade_parts, ignore_index=True) if trade_parts else empty_trades()
    events = normalize_events(pd.DataFrame(event_rows, columns=EVENT_COLUMNS))
    report = FilingsReport(
        n_companies=len(ciks),
        n_companies_missing=missing,
        n_form4_listed=listed,
        n_form4_fetched=fetched,
        n_form4_failed=failed,
        n_events=len(events),
    )
    logger.info(
        "filings: %d Form 4 listed, %d fetched, %d failed, %d company filings",
        listed,
        fetched,
        failed,
        len(events),
    )
    return FilingsResult(trades=trades, events=events, seen=seen, report=report)
