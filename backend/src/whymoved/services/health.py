"""Collect FDA decisions and Phase 3 trial news for the universe's health care companies."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

from whymoved.adapters.json_http import SourceError
from whymoved.domain.health import (
    HEALTH_COLUMNS,
    HEALTH_SECTOR,
    SponsorMatcher,
    device_approvals,
    drug_approvals,
    empty_health,
    normalize_health,
    recalls,
    sponsor_alias,
    trial_events,
)
from whymoved.domain.universe import UniverseMember

logger = logging.getLogger(__name__)

TRIAL_KINDS = ("trial_results", "trial_stopped")


class FdaSource(Protocol):
    def drug_applications(self, since: date, until: date) -> list[Mapping[str, object]]: ...

    def device_approvals(self, since: date, until: date) -> list[Mapping[str, object]]: ...

    def recalls(self, kind: str, since: date, until: date) -> list[Mapping[str, object]]: ...


class TrialSource(Protocol):
    def phase3_studies(self, sponsor: str, since: date) -> list[Mapping[str, object]]: ...


@dataclass(frozen=True)
class HealthReport:
    n_companies: int
    n_events: int
    failed_kinds: tuple[str, ...]  # FDA sources that could not be read
    failed_tickers: tuple[str, ...]  # companies whose trials could not be read


def collect_health(
    fda: FdaSource,
    trials: TrialSource,
    members: Sequence[UniverseMember],
    since: date,
    until: date,
) -> tuple[pd.DataFrame, HealthReport]:
    matcher = SponsorMatcher.for_members(members)
    start = pd.Timestamp(since)
    rows: list[dict[str, object]] = []
    failed_kinds: list[str] = []
    try:
        rows += drug_approvals(fda.drug_applications(since, until), matcher, start)
    except SourceError as exc:
        failed_kinds.append("drug_approval")
        logger.warning("drug approvals: %s", exc)
    try:
        rows += device_approvals(fda.device_approvals(since, until), matcher, start)
    except SourceError as exc:
        failed_kinds.append("device_approval")
        logger.warning("device approvals: %s", exc)
    try:
        records = fda.recalls("drug", since, until) + fda.recalls("device", since, until)
        rows += recalls(records, matcher, start)
    except SourceError as exc:
        failed_kinds.append("recall")
        logger.warning("recalls: %s", exc)

    health = [m for m in members if m.sector == HEALTH_SECTOR]
    failed_tickers: list[str] = []
    for m in health:
        try:
            studies = trials.phase3_studies(sponsor_alias(m.name), since)
        except SourceError as exc:
            failed_tickers.append(m.ticker)
            logger.warning("%s trials: %s", m.ticker, exc)
            continue
        rows += trial_events(studies, m.ticker, matcher, start)

    frame = normalize_health(pd.DataFrame(rows, columns=HEALTH_COLUMNS)) if rows else empty_health()
    return frame, HealthReport(
        n_companies=len(health),
        n_events=len(frame),
        failed_kinds=tuple(failed_kinds),
        failed_tickers=tuple(failed_tickers),
    )


def merge_health(stored: pd.DataFrame, fresh: pd.DataFrame, report: HealthReport) -> pd.DataFrame:
    """Tonight's events replace stored ones, except where a source failed tonight: those keep
    last night's rows."""
    keep = stored["kind"].isin(list(report.failed_kinds)) | (
        stored["kind"].isin(list(TRIAL_KINDS)) & stored["ticker"].isin(list(report.failed_tickers))
    )
    return normalize_health(pd.concat([stored[keep], fresh], ignore_index=True))
