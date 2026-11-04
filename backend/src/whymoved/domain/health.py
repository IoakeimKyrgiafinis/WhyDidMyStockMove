"""FDA decisions and clinical-trial news for health care companies.

Kinds of event, each with the day it became public:

- Drug approval: a new drug (an NDA or BLA original) or a new use of one (an efficacy
  supplement), from FDA's Drugs@FDA. Generics and labeling changes are routine and left out.
- Device approval: a premarket approval (PMA) original. Supplements are routine and left out.
- Recall: a Class I recall (the most serious class), dated by FDA's weekly enforcement report.
- Trial results: Phase 3 results first posted on ClinicalTrials.gov.
- Trial stopped: a Phase 3 trial terminated, suspended or withdrawn, dated by the record update.

None of these sources gives a time of day, so an event counts from the session after its date,
the same rule as insider filings: it gives up the day's reaction rather than risk look-ahead.

Companies are matched by name. FDA abbreviates ("BAXTER HLTHCARE"), so a sponsor matches when
its name starts with the company's name or shares a distinctive first word with it. Sponsors
that are subsidiaries with a different name (Janssen for Johnson & Johnson) are missed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from whymoved.domain.universe import UniverseMember
from whymoved.domain.world_news import company_alias

HEALTH_SECTOR = "Health Care"
HEALTH_COLUMNS = ["ticker", "date", "kind", "title", "detail", "url"]
HealthKind = Literal["drug_approval", "device_approval", "recall", "trial_results", "trial_stopped"]
KIND_LABELS: dict[str, str] = {
    "drug_approval": "FDA drug approval",
    "device_approval": "FDA device approval",
    "recall": "Class I recall",
    "trial_results": "Phase 3 results posted",
    "trial_stopped": "Phase 3 trial stopped",
}
MIN_FIRST_WORD = 5
# First words shared by many unrelated companies.
_GENERIC_FIRST = frozenset(
    {
        "american",
        "united",
        "general",
        "national",
        "international",
        "global",
        "advanced",
        "applied",
        "medical",
        "health",
        "healthcare",
        "pacific",
        "western",
        "eastern",
        "southern",
        "northern",
        "great",
        "royal",
        "universal",
        "integrated",
        "precision",
        "standard",
        "pharma",
        "pharmaceuticals",
        "therapeutics",
        "biosciences",
        "biotherapeutics",
        "clinical",
        "molecular",
        "genetic",
        "surgical",
        "dental",
        "vision",
        "first",
        "select",
        "premier",
        "alpha",
        "omega",
    }
)
_EXTRA_SUFFIX = re.compile(r"\b(gmbh|bv|b v|ab|as|a s|sarl|s a r l|kg|spa|s p a|pty|kk)\b")
_SPACE = re.compile(r"\s+")


def sponsor_alias(name: str) -> str:
    return _SPACE.sub(" ", _EXTRA_SUFFIX.sub(" ", company_alias(name))).strip()


@dataclass(frozen=True)
class SponsorMatcher:
    """Matches FDA sponsors and trial sponsors to health care companies in the universe."""

    names: tuple[tuple[str, tuple[str, ...]], ...]  # (ticker, alias words)

    @classmethod
    def for_members(cls, members: Sequence[UniverseMember]) -> SponsorMatcher:
        names = tuple(
            (m.ticker, tuple(sponsor_alias(m.name).split()))
            for m in members
            if m.sector == HEALTH_SECTOR and sponsor_alias(m.name)
        )
        return cls(names=names)

    def tickers(self, sponsor: str) -> set[str]:
        words = sponsor_alias(sponsor).split()
        if not words:
            return set()
        exact = {t for t, alias in self.names if tuple(words[: len(alias)]) == alias}
        if exact:
            return exact
        loose = [
            (t, alias)
            for t, alias in self.names
            if words[0] == alias[0]
            and len(alias[0]) >= MIN_FIRST_WORD
            and alias[0] not in _GENERIC_FIRST
        ]
        if len(loose) <= 1:
            return {t for t, _ in loose}
        # Several companies share the first word ("ACADIA PHARMS": Acadia Pharmaceuticals or
        # Acadia Healthcare?): the second word, often abbreviated by FDA, decides.
        second = words[1] if len(words) > 1 else ""
        decided = {
            t for t, alias in loose if second and len(alias) > 1 and _same_word(second, alias[1])
        }
        return decided if len(decided) == 1 else set()


def _same_word(short: str, full: str) -> bool:
    """FDA's abbreviation of a word: "pharms" for "pharmaceuticals", "hlthcare" for
    "healthcare" (same first letters, then the rest in order)."""
    if short == full or (len(short) >= 4 and full.startswith(short[:4]) and short[0] == full[0]):
        return True
    rest = iter(full)
    return len(short) >= 3 and short[0] == full[0] and all(ch in rest for ch in short)


def _date(raw: object) -> pd.Timestamp | None:
    text = str(raw or "").strip()
    try:
        return pd.Timestamp(text) if text else None
    except ValueError:
        return None


def _text(value: object) -> str:
    return _SPACE.sub(" ", str(value or "")).strip()


def _first(values: object) -> str:
    if isinstance(values, list) and values:
        return _text(values[0])
    return ""


def drug_approvals(
    applications: Iterable[Mapping[str, object]], matcher: SponsorMatcher, since: pd.Timestamp
) -> list[dict[str, object]]:
    """New drugs and new uses (NDA or BLA originals and efficacy supplements) approved since
    `since`."""
    rows: list[dict[str, object]] = []
    for app in applications:
        number = _text(app.get("application_number"))
        if not number.startswith(("NDA", "BLA")):
            continue  # ANDAs are generics
        tickers = matcher.tickers(_text(app.get("sponsor_name")))
        if not tickers:
            continue
        products = app.get("products")
        brand = ""
        if isinstance(products, list) and products and isinstance(products[0], Mapping):
            brand = _text(products[0].get("brand_name")).title()
        labels = app.get("openfda")
        generic = _first(labels.get("generic_name")) if isinstance(labels, Mapping) else ""
        submissions = app.get("submissions")
        for sub in submissions if isinstance(submissions, list) else []:
            if not isinstance(sub, Mapping) or sub.get("submission_status") != "AP":
                continue
            kind = _text(sub.get("submission_type"))
            klass = _text(sub.get("submission_class_code"))
            if kind == "ORIG":
                what = "New drug approved"
            elif kind == "SUPPL" and klass == "EFFICACY":
                what = "New use approved"
            else:
                continue
            day = _date(sub.get("submission_status_date"))
            if day is None or day < since:
                continue
            name = brand or generic.title() or number
            detail = ", ".join(
                p
                for p in (
                    generic.lower() if generic and generic.lower() != name.lower() else "",
                    _text(sub.get("review_priority")).lower() + " review"
                    if sub.get("review_priority")
                    else "",
                    number,
                )
                if p
            )
            digits = re.sub(r"\D", "", number)
            for ticker in tickers:
                rows.append(
                    {
                        "ticker": ticker,
                        "date": day,
                        "kind": "drug_approval",
                        "title": f"{what}: {name}",
                        "detail": detail,
                        "url": "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm"
                        f"?event=overview.process&ApplNo={digits}",
                    }
                )
    return rows


def device_approvals(
    records: Iterable[Mapping[str, object]], matcher: SponsorMatcher, since: pd.Timestamp
) -> list[dict[str, object]]:
    """Premarket approvals (originals, not supplements) since `since`."""
    rows: list[dict[str, object]] = []
    for r in records:
        if _text(r.get("supplement_number")) or _text(r.get("decision_code")) != "APPR":
            continue
        day = _date(r.get("decision_date"))
        if day is None or day < since:
            continue
        number = _text(r.get("pma_number"))
        for ticker in matcher.tickers(_text(r.get("applicant"))):
            rows.append(
                {
                    "ticker": ticker,
                    "date": day,
                    "kind": "device_approval",
                    "title": f"Device approved: {_text(r.get('trade_name'))}",
                    "detail": f"{_text(r.get('generic_name')).lower()}, {number}".strip(", "),
                    "url": "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpma/pma.cfm"
                    f"?id={number}",
                }
            )
    return rows


def recalls(
    records: Iterable[Mapping[str, object]], matcher: SponsorMatcher, since: pd.Timestamp
) -> list[dict[str, object]]:
    """Class I recalls, one row per recall event (an event can cover many products)."""
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for r in records:
        if _text(r.get("classification")) != "Class I":
            continue
        day = _date(r.get("report_date"))
        if day is None or day < since:
            continue
        event = _text(r.get("event_id")) or _text(r.get("recall_number"))
        product = _text(r.get("product_description"))
        for ticker in matcher.tickers(_text(r.get("recalling_firm"))):
            if (ticker, event) in seen:
                continue
            seen.add((ticker, event))
            rows.append(
                {
                    "ticker": ticker,
                    "date": day,
                    "kind": "recall",
                    "title": f"Class I recall: {product[:140]}",
                    "detail": _text(r.get("reason_for_recall"))[:400],
                    "url": f"https://www.accessdata.fda.gov/scripts/ires/index.cfm?Event={event}",
                }
            )
    return rows


_STOPPED = {"TERMINATED": "terminated", "SUSPENDED": "suspended", "WITHDRAWN": "withdrawn"}


def trial_events(
    studies: Iterable[Mapping[str, object]],
    ticker: str,
    matcher: SponsorMatcher,
    since: pd.Timestamp,
) -> list[dict[str, object]]:
    """Phase 3 results posted and Phase 3 trials stopped, for studies led by the company."""
    rows: list[dict[str, object]] = []
    for study in studies:
        section = study.get("protocolSection")
        if not isinstance(section, Mapping):
            continue
        ident = section.get("identificationModule") or {}
        status = section.get("statusModule") or {}
        sponsors = section.get("sponsorCollaboratorsModule") or {}
        if not isinstance(ident, Mapping) or not isinstance(status, Mapping):
            continue
        lead = sponsors.get("leadSponsor") if isinstance(sponsors, Mapping) else None
        lead_name = _text(lead.get("name")) if isinstance(lead, Mapping) else ""
        if ticker not in matcher.tickers(lead_name):
            continue
        nct = _text(ident.get("nctId"))
        title = _text(ident.get("briefTitle"))
        url = f"https://clinicaltrials.gov/study/{nct}"
        posted = status.get("resultsFirstPostDateStruct")
        day = _date(posted.get("date")) if isinstance(posted, Mapping) else None
        if day is not None and day >= since:
            rows.append(
                {
                    "ticker": ticker,
                    "date": day,
                    "kind": "trial_results",
                    "title": f"Phase 3 results posted: {title}",
                    "detail": nct,
                    "url": url,
                }
            )
        overall = _text(status.get("overallStatus"))
        if overall in _STOPPED:
            updated = status.get("lastUpdatePostDateStruct")
            day = _date(updated.get("date")) if isinstance(updated, Mapping) else None
            if day is not None and day >= since:
                why = _text(status.get("whyStopped"))
                rows.append(
                    {
                        "ticker": ticker,
                        "date": day,
                        "kind": "trial_stopped",
                        "title": f"Phase 3 trial {_STOPPED[overall]}: {title}",
                        "detail": f"{nct}{f': {why}' if why else ''}",
                        "url": url,
                    }
                )
    return rows


def normalize_health(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in HEALTH_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"health events are missing columns: {missing}")
    data = frame[HEALTH_COLUMNS].reset_index(drop=True)
    out = pd.DataFrame(
        {
            "ticker": data["ticker"].astype(str),
            "date": pd.to_datetime(data["date"]).dt.as_unit("ns"),
            "kind": data["kind"].astype(str),
            "title": data["title"].astype(str),
            "detail": data["detail"].astype(str),
            "url": data["url"].astype(str),
        }
    )
    out = out[out["kind"].isin(list(KIND_LABELS))]
    return (
        out.drop_duplicates(subset=["ticker", "kind", "url", "date"])
        .sort_values(["date", "ticker", "kind"], kind="mergesort")
        .reset_index(drop=True)
    )


def empty_health() -> pd.DataFrame:
    return normalize_health(pd.DataFrame({c: [] for c in HEALTH_COLUMNS}))
