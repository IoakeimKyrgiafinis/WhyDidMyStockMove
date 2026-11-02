from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from market import (
    BUY_DAYS,
    EARNINGS_DAYS,
    INTRADAY_8K_DAY,
    NEWS_DAY,
    day_at,
    make_market,
    write_snapshot,
)
from whymoved.adapters.snapshot import load_snapshot
from whymoved.jobs import insider_study
from whymoved.main import create_app
from whymoved.services.explain import MoveExplainer


@pytest.fixture(scope="module")
def market():  # type: ignore[no-untyped-def]
    return make_market(with_events=True)


@pytest.fixture(scope="module")
def explainer(market) -> MoveExplainer:  # type: ignore[no-untyped-def]
    return MoveExplainer(market.snapshot, n_boot=200)


@pytest.fixture(scope="module")
def client(explainer: MoveExplainer) -> TestClient:
    return TestClient(create_app(explainer))


# ---- placing items on trading days ----


def test_headlines_land_on_the_session_that_could_react(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    h = explainer.timeline.headlines("AAA")

    days = sorted(pd.Timestamp(d).date() for d in h["day"])
    # 10:00 New York -> same day; 17:30 New York -> the next trading day
    assert days == [day_at(market, NEWS_DAY), day_at(market, NEWS_DAY + 2)]
    assert len(explainer.timeline.headlines("BBB")) == 1  # the shared headline


def test_after_close_8ks_move_to_the_next_day(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    f = explainer.timeline.filings("BBB")

    earnings_days = sorted(pd.Timestamp(d).date() for d in f.loc[f["earnings"], "day"])
    assert earnings_days == [day_at(market, p) for p in EARNINGS_DAYS]
    intraday = f.loc[~f["earnings"], "day"].iloc[0]
    assert pd.Timestamp(intraday).date() == day_at(market, INTRADAY_8K_DAY)
    assert "Results of operations" in f.loc[f["earnings"], "labels"].iloc[0][0]
    assert f["url"].iloc[0].startswith("https://www.sec.gov/Archives/edgar/data/1002/")


def test_form4s_count_from_the_day_after_filing(market, explainer: MoveExplainer) -> None:  # type: ignore[no-untyped-def]
    i = explainer.timeline.insiders("AAA")

    assert sorted(pd.Timestamp(d).date() for d in i["day"]) == [day_at(market, p) for p in BUY_DAYS]
    assert set(i["signal"]) == {"buy"}
    assert set(explainer.timeline.insiders("BBB")["signal"]) == {"planned_sale", "routine"}
    assert explainer.timeline.insiders("ABC").empty


# ---- statistics ----


def test_the_planted_post_purchase_drift_is_found(explainer: MoveExplainer) -> None:
    study = explainer.timeline.insider_study("AAA")

    assert study.windows.n_used == len(BUY_DAYS)
    five = next(t for t in study.tests if (t.start, t.end) == (0, 5))
    assert five.mean_car == pytest.approx(5 * 0.005, abs=0.012)
    assert five.p_value < 0.01
    assert study.permutation is not None
    assert study.permutation.p_value < 0.05


def test_a_stock_without_purchases_has_no_study(explainer: MoveExplainer) -> None:
    study = explainer.timeline.insider_study("ABC")

    assert study.windows.n_requested == 0
    assert study.path is None


def test_earnings_days_move_more_than_ordinary_days(explainer: MoveExplainer) -> None:
    reaction = explainer.timeline.earnings_reaction("BBB")

    assert reaction is not None
    assert reaction.ratio > 3.0
    assert reaction.p_value < 0.01
    assert explainer.timeline.earnings_reaction("AAA") is None


# ---- API ----


def test_markers(client: TestClient, market) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/stocks/BBB/markers").json()

    by_date = {m["date"]: m for m in body["markers"]}
    first = by_date[day_at(market, EARNINGS_DAYS[0]).isoformat()]
    assert first["earnings"] is True and first["filings"] == 1
    # a routine award gets no marker, a planned sale does
    assert sum(m["sales"] for m in body["markers"]) == 1
    assert sum(m["headlines"] for m in body["markers"]) == 1


def test_a_day_lists_what_landed_on_it(client: TestClient, market) -> None:  # type: ignore[no-untyped-def]
    when = day_at(market, EARNINGS_DAYS[0]).isoformat()

    body = client.get(f"/api/v1/stocks/BBB/days/{when}").json()

    assert [f["items"] for f in body["items"]["filings"]] == [["2.02", "9.01"]]
    assert body["items"]["headlines"] == []
    assert abs(body["unexplained"]) > 0.03


def test_insiders_endpoint(client: TestClient) -> None:
    body = client.get("/api/v1/stocks/AAA/insiders").json()

    assert body["counts"] == {"buy": len(BUY_DAYS)}
    assert body["filings"][0]["signal_label"] == "Open-market purchase"
    assert body["filings"][0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/1001/")
    assert body["study"]["n_used"] == len(BUY_DAYS)
    assert len(body["study"]["mean_car"]) == 26
    assert body["earnings"] is None
    assert body["pooled"] is None


def test_insiders_endpoint_without_insider_data(client: TestClient) -> None:
    body = client.get("/api/v1/stocks/ABC/insiders").json()

    assert body["filings"] == [] and body["study"] is None


# ---- snapshot round trip and the pooled batch job ----


def test_pooled_study_job_end_to_end(tmp_path: Path, market) -> None:  # type: ignore[no-untyped-def]
    write_snapshot(tmp_path, market)

    assert insider_study.main(["--data-dir", str(tmp_path)]) == 0

    result = json.loads((tmp_path / "insider_study.json").read_text(encoding="utf-8"))
    buy = result["studies"]["buy"]
    assert buy["n_events"] == len(BUY_DAYS)
    assert buy["n_clusters"] == len(BUY_DAYS)
    assert buy["tests"][1]["mean_car"] > 0.01

    # The API serves it after loading the same directory.
    client = TestClient(create_app(MoveExplainer(load_snapshot(tmp_path), n_boot=200)))
    pooled = client.get("/api/v1/stocks/AAA/insiders").json()["pooled"]
    assert pooled["studies"]["buy"]["n_events"] == len(BUY_DAYS)


def test_the_job_fails_clearly_without_insider_data(tmp_path: Path) -> None:
    write_snapshot(tmp_path, make_market())

    assert insider_study.main(["--data-dir", str(tmp_path)]) == 1


def test_a_broken_optional_file_does_not_stop_loading(tmp_path: Path, market) -> None:  # type: ignore[no-untyped-def]
    write_snapshot(tmp_path, market)
    (tmp_path / "news.parquet").write_bytes(b"not parquet")

    snapshot = load_snapshot(tmp_path)

    assert snapshot.news.empty
    assert len(snapshot.trades) == len(market.snapshot.trades)
    assert str(snapshot.trades["kind"].dtype) == "category"
