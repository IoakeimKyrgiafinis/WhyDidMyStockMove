from __future__ import annotations

import json
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from peer_market import COINTEGRATED, LINKED, make_peer_snapshot
from whymoved.main import create_app
from whymoved.services.explain import MoveExplainer
from whymoved.services.reactions import ReactionService


@pytest.fixture(scope="module")
def explainer() -> MoveExplainer:
    return MoveExplainer(make_peer_snapshot(), n_boot=200)


@pytest.fixture(scope="module")
def client(explainer: MoveExplainer) -> TestClient:
    return TestClient(create_app(explainer, reactions=ReactionService(None)))


def events(text: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_peers_json(client: TestClient) -> None:
    body = client.get("/api/v1/stocks/AAA/peers").json()

    assert body["target"] == "AAA"
    assert {p["ticker"] for p in body["peers"][:6]} == {*LINKED, COINTEGRATED}
    n = len(body["order"])
    assert n == len(body["peers"]) + 1
    assert set(body["grids"]) == {"residual", "raw", "spillover"}
    assert all(len(row) == n for row in body["grids"]["residual"])
    coint = {row["ticker"]: row for row in body["cointegration"]}
    assert coint[COINTEGRATED]["cointegrated"] is True
    assert len(coint) == 10


def test_stream_sends_the_grid_then_each_test_then_the_correction(client: TestClient) -> None:
    response = client.get("/api/v1/stocks/AAA/peers/stream")

    assert response.headers["content-type"].startswith("text/event-stream")
    names = [name for name, _ in events(response.text)]
    assert names == ["peers", *["cointegration"] * 10, "fdr", "done"]
    fdr = dict(events(response.text))["fdr"]
    assert {row["ticker"] for row in fdr if row["cointegrated"]} >= {COINTEGRATED}


def test_comove(client: TestClient, explainer: MoveExplainer) -> None:
    daily = explainer.daily("AAA")
    day = pd.Timestamp(daily.index[-1]).date().isoformat()

    body = client.get("/api/v1/stocks/AAA/peers/comove", params={"day": day}).json()

    assert body["date"] == day
    assert len(body["peers"]) == 10
    first = body["peers"][0]
    assert first["expected"] == pytest.approx(first["beta"] * body["own_unexplained"])


def test_replay_stream(client: TestClient) -> None:
    response = client.get("/api/v1/stocks/AAA/peers/replay")

    parsed = events(response.text)
    assert parsed[0][0] == "names" and parsed[0][1]["names"][0] == "AAA"
    assert parsed[-1][0] == "done"
    frames = [data for name, data in parsed if name == "frame"]
    assert len(frames) > 50
    assert len(frames[0]["residual"]) == 20


@pytest.mark.parametrize(
    ("url", "status", "code"),
    [
        ("/api/v1/stocks/ZZZ/peers", 404, "unknown_ticker"),
        ("/api/v1/stocks/ZZZ/peers/stream", 404, "unknown_ticker"),
        ("/api/v1/stocks/LATE/peers/stream", 422, "not_enough_data"),
        ("/api/v1/stocks/ZZZ/peers/replay", 404, "unknown_ticker"),
        ("/api/v1/stocks/AAA/peers/comove?day=2023-01-03", 422, "not_enough_data"),
        ("/api/v1/stocks/AAA/peers/comove", 422, "invalid_request"),
    ],
)
def test_errors_come_before_any_stream(
    client: TestClient, url: str, status: int, code: str
) -> None:
    response = client.get(url)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
