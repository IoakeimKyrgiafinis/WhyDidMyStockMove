from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import whymoved.jobs.collect_health as job
from market import make_market, write_snapshot
from whymoved.adapters.health_sources import ClinicalTrials, OpenFda
from whymoved.adapters.health_store import ParquetHealthStore
from whymoved.adapters.json_http import JsonHttp, SourceError, SourceUnavailable
from whymoved.adapters.snapshot import load_snapshot
from whymoved.domain.health import (
    HEALTH_COLUMNS,
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
from whymoved.main import create_app
from whymoved.services.explain import MoveExplainer
from whymoved.services.health import HealthReport, collect_health, merge_health

T = pd.Timestamp
SINCE = T("2026-01-01")


def hc(ticker: str, name: str, sector: str = "Health Care") -> UniverseMember:
    return UniverseMember(ticker=ticker, name=name, sector=sector, sector_etf="XLV", weight=1.0)


MEMBERS = [
    hc("PFE", "PFIZER INC"),
    hc("LLY", "ELI LILLY"),
    hc("BAX", "BAXTER INTERNATIONAL INC"),
    hc("ACAD", "ACADIA PHARMACEUTICALS INC"),
    hc("ACHC", "ACADIA HEALTHCARE COMPANY INC"),
    hc("AMT", "AMERICAN TOWER", sector="Real Estate"),
    hc("AMWL", "AMERICAN WELL CORP"),
]
MATCHER = SponsorMatcher.for_members(MEMBERS)


# ---- matching ----


def test_sponsor_names_match_the_company() -> None:
    assert sponsor_alias("Novocure, GmbH") == "novocure"
    assert MATCHER.tickers("PFIZER LABS") == {"PFE"}
    assert MATCHER.tickers("ELI LILLY AND CO") == {"LLY"}
    assert MATCHER.tickers("BAXTER HLTHCARE CORP") == {"BAX"}  # first word, abbreviated rest
    assert MATCHER.tickers("") == set()


def test_shared_first_words_are_decided_by_the_second() -> None:
    assert MATCHER.tickers("ACADIA PHARMS INC") == {"ACAD"}
    assert MATCHER.tickers("ACADIA HLTHCARE") == {"ACHC"}
    assert MATCHER.tickers("ACADIA") == set()  # cannot tell


def test_generic_first_words_and_other_sectors_never_match_loosely() -> None:
    assert MATCHER.tickers("AMERICAN REGENT") == set()
    assert MATCHER.tickers("AMERICAN WELL") == {"AMWL"}  # the full name still matches
    assert MATCHER.tickers("AMERICAN TOWER") == set()  # not a health care company


# ---- FDA and trial records ----


def drug(number: str, sponsor: str, *subs: dict[str, str]) -> dict[str, object]:
    return {
        "application_number": number,
        "sponsor_name": sponsor,
        "products": [{"brand_name": "ZAPRIN"}],
        "openfda": {"generic_name": ["zaprinib"]},
        "submissions": list(subs),
    }


def sub(kind: str, klass: str, day: str, status: str = "AP") -> dict[str, str]:
    return {
        "submission_type": kind,
        "submission_class_code": klass,
        "submission_status": status,
        "submission_status_date": day,
        "review_priority": "PRIORITY",
    }


def test_drug_approvals_keep_new_drugs_and_new_uses() -> None:
    rows = drug_approvals(
        [
            drug(
                "NDA123456",
                "PFIZER INC",
                sub("ORIG", "TYPE 1", "20260310"),
                sub("SUPPL", "EFFICACY", "20260601"),
                sub("SUPPL", "LABELING", "20260602"),
                sub("SUPPL", "EFFICACY", "20260603", status="TA"),
                sub("ORIG", "TYPE 1", "20250101"),  # before the window
                sub("ORIG", "TYPE 1", ""),
            ),
            drug("ANDA999999", "PFIZER INC", sub("ORIG", "UNKNOWN", "20260310")),  # a generic
            drug("NDA000001", "SOMEONE ELSE", sub("ORIG", "TYPE 1", "20260310")),
            {"application_number": "BLA1", "sponsor_name": "PFIZER", "submissions": "bad"},
        ],
        MATCHER,
        SINCE,
    )
    assert [(r["title"], r["date"]) for r in rows] == [
        ("New drug approved: Zaprin", T("2026-03-10")),
        ("New use approved: Zaprin", T("2026-06-01")),
    ]
    assert rows[0]["detail"] == "zaprinib, priority review, NDA123456"
    assert str(rows[0]["url"]).endswith("ApplNo=123456")


def test_device_approvals_keep_originals_only() -> None:
    base = {"applicant": "Baxter Healthcare", "decision_code": "APPR", "trade_name": "Pump"}
    rows = device_approvals(
        [
            {**base, "supplement_number": "", "decision_date": "2026-05-01", "pma_number": "P1"},
            {
                **base,
                "supplement_number": "S001",
                "decision_date": "2026-05-01",
                "pma_number": "P1",
            },
            {**base, "supplement_number": "", "decision_date": "2025-05-01", "pma_number": "P2"},
            {**base, "decision_code": "DENY", "decision_date": "2026-05-01", "pma_number": "P3"},
        ],
        MATCHER,
        SINCE,
    )
    assert [(r["ticker"], r["title"]) for r in rows] == [("BAX", "Device approved: Pump")]
    assert str(rows[0]["url"]).endswith("id=P1")


def test_recalls_are_class_i_and_one_per_event() -> None:
    base = {"recalling_firm": "Baxter Healthcare Corporation", "classification": "Class I"}
    rows = recalls(
        [
            {**base, "report_date": "20260909", "event_id": "1", "product_description": "Saline A"},
            {**base, "report_date": "20260909", "event_id": "1", "product_description": "Saline B"},
            {**base, "report_date": "20260909", "event_id": "2", "classification": "Class II"},
            {**base, "report_date": "bad", "event_id": "3"},
        ],
        MATCHER,
        SINCE,
    )
    assert [r["title"] for r in rows] == ["Class I recall: Saline A"]


def study(
    nct: str, status: str, results: str = "", updated: str = "2026-04-01", lead: str = "Pfizer"
) -> dict[str, object]:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct, "briefTitle": f"Study {nct}"},
            "statusModule": {
                "overallStatus": status,
                "whyStopped": "Lack of efficacy",
                "lastUpdatePostDateStruct": {"date": updated},
                **({"resultsFirstPostDateStruct": {"date": results}} if results else {}),
            },
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": lead}},
        }
    }


def test_trial_results_and_stops() -> None:
    rows = trial_events(
        [
            study("NCT1", "COMPLETED", results="2026-03-01"),
            study("NCT2", "TERMINATED"),
            study("NCT3", "RECRUITING"),
            study("NCT4", "TERMINATED", lead="Someone Else"),
            study("NCT5", "COMPLETED", results="2025-03-01"),
            {"protocolSection": "bad"},
        ],
        "PFE",
        MATCHER,
        SINCE,
    )
    assert [(r["kind"], r["detail"]) for r in rows] == [
        ("trial_results", "NCT1"),
        ("trial_stopped", "NCT2: Lack of efficacy"),
    ]
    assert rows[1]["title"] == "Phase 3 trial terminated: Study NCT2"


def test_normalize_needs_columns_and_drops_unknown_kinds() -> None:
    frame = pd.DataFrame(
        [
            ["PFE", "2026-01-02", "rumour", "t", "d", "u"],
            ["PFE", "2026-01-02", "recall", "t", "d", "u"],
        ],
        columns=HEALTH_COLUMNS,
    )
    assert normalize_health(frame)["kind"].tolist() == ["recall"]
    with pytest.raises(ValueError, match="missing columns"):
        normalize_health(frame.drop(columns=["url"]))


# ---- HTTP sources ----


def http(handler: Callable[[httpx.Request], httpx.Response]) -> JsonHttp:
    return JsonHttp(
        httpx.Client(transport=httpx.MockTransport(handler)), name="T", sleep=lambda _s: None
    )


def test_openfda_pages_through_results() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        skip = int(request.url.params["skip"])
        results = [{"n": skip + i} for i in range(1000 if skip == 0 else 5)]
        return httpx.Response(200, json={"meta": {"results": {"total": 1005}}, "results": results})

    out = OpenFda(http(handler)).drug_applications(date(2026, 1, 1), date(2026, 11, 4))
    assert len(out) == 1005 and len(seen) == 2
    assert (
        seen[0].url.params["search"] == "submissions.submission_status_date:[20260101 TO 20261104]"
    )


def test_openfda_no_matches_and_bad_answers() -> None:
    empty = OpenFda(http(lambda r: httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})))
    assert empty.recalls("drug", date(2026, 1, 1), date(2026, 11, 4)) == []
    assert empty.device_approvals(date(2026, 1, 1), date(2026, 11, 4)) == []
    with pytest.raises(SourceError):
        OpenFda(http(lambda r: httpx.Response(200, json={}))).recalls(
            "device", date(2026, 1, 1), date(2026, 1, 2)
        )
    with pytest.raises(SourceError):
        OpenFda(http(lambda r: httpx.Response(400, text="bad"))).recalls(
            "device", date(2026, 1, 1), date(2026, 1, 2)
        )


def test_clinical_trials_follows_page_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "pageToken" in request.url.params:
            return httpx.Response(200, json={"studies": [{"b": 1}]})
        return httpx.Response(200, json={"studies": [{"a": 1}, "junk"], "nextPageToken": "next"})

    trials = ClinicalTrials(http(handler))
    assert trials.phase3_studies("pfizer", date(2026, 1, 1)) == [{"a": 1}, {"b": 1}]
    with pytest.raises(SourceError):
        ClinicalTrials(http(lambda r: httpx.Response(200, json=[]))).phase3_studies(
            "x", date(2026, 1, 1)
        )


# ---- collecting ----


class FakeFda:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()

    def _check(self, name: str) -> None:
        if name in self.failing:
            raise SourceUnavailable("down")

    def drug_applications(self, since: date, until: date) -> list[Mapping[str, object]]:
        self._check("drug")
        return [drug("NDA123456", "PFIZER INC", sub("ORIG", "TYPE 1", "20260310"))]

    def device_approvals(self, since: date, until: date) -> list[Mapping[str, object]]:
        self._check("device")
        return []

    def recalls(self, kind: str, since: date, until: date) -> list[Mapping[str, object]]:
        self._check("recall")
        return []


class FakeTrials:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.asked: list[str] = []

    def phase3_studies(self, sponsor: str, since: date) -> list[Mapping[str, object]]:
        self.asked.append(sponsor)
        if sponsor in self.failing:
            raise SourceUnavailable("down")
        return [study("NCT9", "TERMINATED", lead=sponsor)]


def test_collect_reads_every_source_and_health_care_company_only() -> None:
    trials = FakeTrials()
    frame, report = collect_health(FakeFda(), trials, MEMBERS, date(2026, 1, 1), date(2026, 11, 4))
    assert "american tower" not in trials.asked
    assert report.n_companies == 6 and not report.failed_kinds
    assert set(frame["kind"]) == {"drug_approval", "trial_stopped"}


def test_a_failed_source_keeps_last_nights_rows() -> None:
    fresh, report = collect_health(
        FakeFda({"drug"}), FakeTrials({"pfizer"}), MEMBERS, date(2026, 1, 1), date(2026, 11, 4)
    )
    assert report.failed_kinds == ("drug_approval",) and report.failed_tickers == ("PFE",)
    stored = normalize_health(
        pd.DataFrame(
            [
                ["PFE", "2026-03-10", "drug_approval", "old approval", "", "u1"],
                ["PFE", "2026-03-11", "trial_stopped", "old stop", "", "u2"],
                ["LLY", "2026-03-11", "trial_stopped", "replaced", "", "u3"],
            ],
            columns=HEALTH_COLUMNS,
        )
    )
    merged = merge_health(stored, fresh, report)
    titles = set(merged["title"])
    assert {"old approval", "old stop"} <= titles and "replaced" not in titles
    assert merge_health(empty_health(), empty_health(), HealthReport(0, 0, (), ())).empty


def test_store_round_trip(tmp_path: Path) -> None:
    store = ParquetHealthStore(tmp_path)
    assert store.load().empty
    frame, _ = collect_health(FakeFda(), FakeTrials(), MEMBERS, date(2026, 1, 1), date(2026, 11, 4))
    store.save(frame)
    pd.testing.assert_frame_equal(store.load(), frame)
    store.path.write_bytes(b"broken")
    with pytest.raises(Exception, match="unreadable"):
        store.load()


def write_universe(data: Path) -> None:
    data.mkdir(parents=True, exist_ok=True)
    members = [
        {"ticker": m.ticker, "name": m.name, "sector": m.sector, "sector_etf": "XLV", "weight": 1.0}
        for m in MEMBERS
    ]
    summary = {"as_of": "2026-11-02", "history_start": "2020-01-02", "members": members}
    (data / "universe.json").write_text(json.dumps(summary), encoding="utf-8")


@pytest.mark.parametrize(
    ("fda", "trials", "code"),
    [(set(), set(), 0), ({"recall"}, set(), 1), (set(), {"pfizer"}, 1)],
)
def test_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fda: set[str], trials: set[str], code: int
) -> None:
    data = tmp_path / "data"
    assert job.main(["--data-dir", str(data)]) == 2  # no universe yet
    write_universe(data)
    monkeypatch.setattr(job, "open_fda", lambda: FakeFda(fda))
    monkeypatch.setattr(job, "clinical_trials", lambda: FakeTrials(trials))
    assert job.main(["--data-dir", str(data)]) == code
    assert not ParquetHealthStore(data).load().empty
    ParquetHealthStore(data).path.write_bytes(b"broken")
    assert job.main(["--data-dir", str(data)]) == 1


# ---- serving ----


def test_events_count_from_the_next_session(tmp_path: Path) -> None:
    market = make_market()
    write_snapshot(tmp_path, market)
    days = market.close.index
    friday = next(d for d in days[-40:] if d.dayofweek == 4)
    events = pd.DataFrame(
        [["AAA", friday, "drug_approval", "New drug approved: Zaprin", "NDA1", "https://fda/1"]],
        columns=HEALTH_COLUMNS,
    )
    ParquetHealthStore(tmp_path).save(events)

    client = TestClient(create_app(MoveExplainer(load_snapshot(tmp_path), n_boot=50)))
    monday = days[days > friday][0]
    body = client.get(f"/api/v1/stocks/AAA/days/{monday.date().isoformat()}").json()
    assert body["items"]["health"] == [
        {
            "kind": "drug_approval",
            "label": "FDA drug approval",
            "date": friday.date().isoformat(),
            "title": "New drug approved: Zaprin",
            "detail": "NDA1",
            "url": "https://fda/1",
        }
    ]
    same_day = client.get(f"/api/v1/stocks/AAA/days/{friday.date().isoformat()}").json()
    assert same_day["items"]["health"] == []
