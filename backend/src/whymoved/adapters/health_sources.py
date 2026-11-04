"""openFDA and ClinicalTrials.gov: public APIs, no key.

openFDA allows 240 requests a minute and 1,000 a day without a key; a nightly run makes about
30. ClinicalTrials.gov asks for about one request a second; a run makes one or two per health
care company.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import httpx

from whymoved.adapters.json_http import JsonHttp, SourceError, SourceRequestError

OPENFDA_URL = "https://api.fda.gov/"
TRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
TIMEOUT = 60.0
PAGE = 1000
MAX_SKIP = 25_000  # openFDA refuses a larger skip
TRIAL_FIELDS = ",".join(
    [
        "NCTId",
        "BriefTitle",
        "OverallStatus",
        "WhyStopped",
        "ResultsFirstPostDate",
        "LastUpdatePostDate",
        "LeadSponsorName",
    ]
)


def _results(payload: object, name: str) -> tuple[list[Mapping[str, object]], int]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SourceError(f"openFDA: unexpected {name} response")
    meta = payload.get("meta")
    total = 0
    if isinstance(meta, dict) and isinstance(meta.get("results"), dict):
        total = int(meta["results"].get("total") or 0)
    return [r for r in payload["results"] if isinstance(r, dict)], total


class OpenFda:
    def __init__(self, http: JsonHttp) -> None:
        self._http = http

    def search(self, endpoint: str, query: str) -> list[Mapping[str, object]]:
        """Every record matching `query` (up to openFDA's paging limit)."""
        out: list[Mapping[str, object]] = []
        skip = 0
        while skip <= MAX_SKIP:
            try:
                payload = self._http.get(
                    OPENFDA_URL + endpoint,
                    {"search": query, "limit": str(PAGE), "skip": str(skip)},
                )
            except SourceRequestError as exc:
                if exc.status == 404:  # openFDA's answer when nothing matches
                    return out
                raise
            records, total = _results(payload, endpoint)
            out += records
            skip += PAGE
            if not records or skip >= total:
                break
        return out

    def drug_applications(self, since: date, until: date) -> list[Mapping[str, object]]:
        return self.search(
            "drug/drugsfda.json",
            f"submissions.submission_status_date:[{since:%Y%m%d} TO {until:%Y%m%d}]",
        )

    def device_approvals(self, since: date, until: date) -> list[Mapping[str, object]]:
        return self.search("device/pma.json", f"decision_date:[{since} TO {until}]")

    def recalls(self, kind: str, since: date, until: date) -> list[Mapping[str, object]]:
        return self.search(
            f"{kind}/enforcement.json",
            f'classification:"Class I" AND report_date:[{since:%Y%m%d} TO {until:%Y%m%d}]',
        )


class ClinicalTrials:
    def __init__(self, http: JsonHttp) -> None:
        self._http = http

    def phase3_studies(self, sponsor: str, since: date) -> list[Mapping[str, object]]:
        """Phase 3 studies led by `sponsor` (a name search) whose record changed since `since`."""
        params = {
            "query.lead": sponsor,
            "filter.advanced": f"AREA[Phase]PHASE3 AND AREA[LastUpdatePostDate]RANGE[{since},MAX]",
            "fields": TRIAL_FIELDS,
            "pageSize": "1000",
        }
        out: list[Mapping[str, object]] = []
        for _ in range(20):
            payload = self._http.get(TRIALS_URL, params)
            if not isinstance(payload, dict) or not isinstance(payload.get("studies"), list):
                raise SourceError("ClinicalTrials.gov: unexpected response")
            out += [s for s in payload["studies"] if isinstance(s, dict)]
            token = payload.get("nextPageToken")
            if not isinstance(token, str) or not token:
                break
            params = {**params, "pageToken": token}
        return out


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "whystockmoved"})


def open_fda() -> OpenFda:
    return OpenFda(JsonHttp(_client(), name="openFDA", min_interval=0.3))


def clinical_trials() -> ClinicalTrials:
    return ClinicalTrials(JsonHttp(_client(), name="ClinicalTrials.gov", min_interval=1.0))
