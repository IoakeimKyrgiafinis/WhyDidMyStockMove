from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from market import make_market, shock_date, write_snapshot
from whymoved.api.state import DataState
from whymoved.config import Settings
from whymoved.main import create_app
from whymoved.services.explain import MoveExplainer


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(MoveExplainer(make_market().snapshot, n_boot=200)))


def test_meta_reports_the_loaded_data(client: TestClient) -> None:
    body = client.get("/api/v1/meta").json()

    assert body["status"] == "ready"
    assert body["n_stocks"] == 4
    assert body["window"] == 252


def test_search(client: TestClient) -> None:
    body = client.get("/api/v1/stocks", params={"q": "bank"}).json()

    assert body["results"] == [
        {"ticker": "BBB", "name": "BETA BANK", "sector": "Financials", "sector_etf": "XLF"}
    ]


def test_profile(client: TestClient) -> None:
    body = client.get("/api/v1/stocks/aaa").json()

    assert body["stock"]["ticker"] == "AAA"
    shares = [body["variance"][k]["estimate"] for k in ("market", "sector", "own")]
    assert sum(shares) == pytest.approx(1.0)
    assert body["health"]["status"] in ("good", "caution", "poor")


def test_daily_is_columnar_and_aligned(client: TestClient) -> None:
    body = client.get("/api/v1/stocks/AAA/daily").json()

    n = len(body["dates"])
    assert n == 699 - 252
    for key in ("total", "market", "sector", "drift", "unexplained", "z", "p_value", "flagged"):
        assert len(body[key]) == n
    assert body["z"][0] is None  # no volatility estimate yet: null, never NaN


def test_day_flags_the_planted_shock(client: TestClient) -> None:
    when = shock_date(make_market()).isoformat()

    body = client.get(f"/api/v1/stocks/AAA/days/{when}").json()

    assert body["flagged"] is True
    assert body["unexplained"] < -0.05
    total = body["market"] + body["sector"] + body["drift"] + body["unexplained"]
    assert total == pytest.approx(body["total"])


def test_attribution(client: TestClient) -> None:
    body = client.get(
        "/api/v1/stocks/AAA/attribution", params={"start": "2024-06-03", "end": "2024-12-31"}
    ).json()

    assert body["start"] == "2024-06-03"
    assert body["market"] + body["sector"] + body["own"] == pytest.approx(body["sum_of_days"])
    assert body["compounding"] == pytest.approx(body["compounded"] - body["sum_of_days"])


@pytest.mark.parametrize(
    ("url", "status", "code"),
    [
        ("/api/v1/stocks/ZZZ", 404, "unknown_ticker"),
        ("/api/v1/stocks/NEW/daily", 422, "not_enough_data"),
        ("/api/v1/stocks/AAA/days/2023-01-03", 422, "not_enough_data"),
        ("/api/v1/stocks/AAA/days/not-a-date", 422, "invalid_request"),
        ("/api/v1/stocks/AAA/attribution?start=2025-01-01&end=2024-01-01", 422, "invalid_request"),
        ("/api/v1/nope", 404, "not_found"),
    ],
)
def test_errors_share_one_format(client: TestClient, url: str, status: int, code: str) -> None:
    response = client.get(url)

    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)


def test_data_endpoints_answer_503_until_data_loads() -> None:
    client = TestClient(create_app())  # no lifespan without a context manager: never loads

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/v1/meta").json()["status"] == "loading"
    response = client.get("/api/v1/stocks")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "data_unavailable"


def test_startup_loads_the_snapshot_from_the_data_dir(tmp_path: Path) -> None:
    write_snapshot(tmp_path, make_market())
    settings = Settings(data_dir=tmp_path, snapshot_url="")

    with TestClient(create_app(settings=settings)) as client:
        for _ in range(100):
            if client.get("/api/v1/meta").json()["status"] != "loading":
                break
        assert client.get("/api/v1/meta").json()["status"] == "ready"


def test_a_missing_snapshot_is_reported_not_fatal(tmp_path: Path) -> None:
    state = DataState()

    state.refresh(tmp_path, "")

    assert state.status()[0] == "unavailable"
    assert "no price files" in (state.status()[1] or "")


def test_refresh_reloads_only_changed_data_and_frees_the_old_copy_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from whymoved.api import state as state_module

    write_snapshot(tmp_path, make_market())
    data = DataState()
    data.refresh(tmp_path, "")
    first = data.explainer

    changes = iter([False, True])
    monkeypatch.setattr(state_module, "download_snapshot", lambda *_a, **_k: next(changes))
    data.refresh(tmp_path, "https://example.test/")
    assert data.explainer is first  # unchanged: not reloaded

    seen_during_load: list[str] = []
    from whymoved.adapters.snapshot import load_snapshot as real_load

    def spying_load(path: Path):  # type: ignore[no-untyped-def]
        seen_during_load.append(data.status()[0])
        return real_load(path)

    monkeypatch.setattr(state_module, "load_snapshot", spying_load)
    data.refresh(tmp_path, "https://example.test/")

    assert seen_during_load == ["loading"]  # the old copy was released before loading
    assert data.explainer is not first
    assert data.status()[0] == "ready"
