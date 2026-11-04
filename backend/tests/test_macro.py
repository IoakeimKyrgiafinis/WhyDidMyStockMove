from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Self

import httpx
import pandas as pd
import pytest

import whymoved.api.convert as convert
import whymoved.jobs.collect_macro as collect_macro
from whymoved.adapters.fred import FredClient
from whymoved.adapters.json_http import (
    JsonHttp,
    SourceError,
    SourceRequestError,
    SourceUnavailable,
)
from whymoved.adapters.macro_store import ParquetMacroStore
from whymoved.domain.macro import (
    FOMC,
    SERIES_BY_KEY,
    empty_macro,
    fomc_rows,
    initial_rows,
    normalize_macro,
    period_label,
)
from whymoved.services.macro import collect_macro as collect
from whymoved.services.macro import merge_macro

T = pd.Timestamp


# ---- domain ----


def test_each_release_carries_the_first_print_of_the_period_before() -> None:
    cpi = SERIES_BY_KEY["cpi"]
    rows = initial_rows(
        cpi,
        [(T("2026-09-11"), T("2026-08-01"), 0.4), (T("2026-08-12"), T("2026-07-01"), 0.2)],
    )
    assert [r["period"] for r in rows] == [T("2026-07-01"), T("2026-08-01")]
    assert rows[0]["previous"] is None
    assert rows[1]["previous"] == 0.2
    assert rows[1]["release_date"] == T("2026-09-11")


def test_scaled_series_are_shown_in_thousands() -> None:
    rows = initial_rows(SERIES_BY_KEY["claims"], [(T("2026-10-30"), T("2026-10-24"), 231000.0)])
    assert rows[0]["value"] == pytest.approx(231.0)


def test_fed_decisions_compare_the_rate_the_day_before_and_after() -> None:
    upper = pd.Series(
        [5.5, 5.5, 5.25, 5.25],
        index=[T("2026-09-15"), T("2026-09-16"), T("2026-09-17"), T("2026-09-18")],
    )
    meetings = [T("2026-09-16"), T("2026-07-29"), T("2026-12-09")]
    rows = fomc_rows(meetings, upper, until=T("2026-11-04"))
    # July is before the data starts, December is still scheduled.
    assert rows == [
        {
            "release_date": T("2026-09-16"),
            "key": FOMC,
            "period": T("2026-09-16"),
            "value": 5.25,
            "previous": 5.5,
        }
    ]


def test_a_decision_whose_new_rate_is_not_out_yet_is_skipped() -> None:
    upper = pd.Series([5.5], index=[T("2026-11-04")])
    assert fomc_rows([T("2026-11-04")], upper, until=T("2026-11-04")) == []


def test_period_labels() -> None:
    assert period_label(SERIES_BY_KEY["cpi"], T("2026-09-01")) == "Sep 2026"
    assert period_label(SERIES_BY_KEY["gdp"], T("2026-07-01")) == "Q3 2026"
    assert period_label(SERIES_BY_KEY["claims"], T("2026-10-24")) == "week to Oct 24"
    assert period_label(SERIES_BY_KEY[FOMC], T("2026-09-16")) == "meeting"


def test_normalize_drops_unknown_series_and_duplicates() -> None:
    frame = pd.DataFrame(
        [
            ["2026-09-11", "cpi", "2026-08-01", 0.3, 0.2],
            ["2026-09-11", "cpi", "2026-08-01", 0.4, 0.2],
            ["2026-09-11", "gold", "2026-08-01", 1.0, None],
        ],
        columns=["release_date", "key", "period", "value", "previous"],
    )
    out = normalize_macro(frame)
    assert out["key"].tolist() == ["cpi"]
    assert out["value"].tolist() == [0.4]
    with pytest.raises(ValueError, match="missing columns"):
        normalize_macro(frame.drop(columns=["previous"]))


def test_api_rows_follow_the_calendar_order_and_mark_changes() -> None:
    frame = normalize_macro(
        pd.DataFrame(
            [
                ["2026-10-03", "unemployment", "2026-09-01", 4.2, 4.3],
                ["2026-10-03", "payrolls", "2026-09-01", 142.0, None],
            ],
            columns=["release_date", "key", "period", "value", "previous"],
        )
    )
    out = convert.macro(frame)
    assert [m.key for m in out] == ["payrolls", "unemployment"]
    assert out[0].signed and not out[1].signed
    assert out[0].previous is None
    assert out[1].period == "Sep 2026"
    assert out[1].url == "https://fred.stlouisfed.org/series/UNRATE"


# ---- HTTP ----


def http(
    handler: Callable[[httpx.Request], httpx.Response],
    sleeps: list[float] | None = None,
    redact: dict[str, str] | None = None,
) -> JsonHttp:
    return JsonHttp(
        httpx.Client(transport=httpx.MockTransport(handler)),
        name="Test",
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
        redact=redact,
    )


def test_server_errors_are_retried_with_retry_after() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, text="slow down")
        if len(calls) == 2:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"ok": True})

    assert http(handler, sleeps).get("https://x.test/", {}) == {"ok": True}
    assert sleeps[:1] == [3.0] and len(calls) == 3


def test_persistent_failures_and_refusals() -> None:
    with pytest.raises(SourceUnavailable):
        http(lambda r: httpx.Response(500, text="down")).get("https://x.test/", {})

    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"bad key {request.url.params['api_key']}")

    with pytest.raises(SourceRequestError) as info:
        http(refused, redact={"sekret": "<key>"}).get("https://x.test/", {"api_key": "sekret"})
    assert info.value.status == 400
    assert "sekret" not in str(info.value) and "<key>" in str(info.value)

    with pytest.raises(SourceError, match="not JSON"):
        http(lambda r: httpx.Response(200, text="<html>")).get("https://x.test/", {})


def test_network_errors_are_retried_then_reported() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(SourceUnavailable, match="network error"):
        http(broken).get("https://x.test/", {})


def fred(responses: dict[str, object], seen: list[httpx.Request]) -> FredClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=responses[request.url.path.rsplit("/fred/", 1)[1]])

    return FredClient(http(handler), "k")


def test_fred_initial_releases() -> None:
    seen: list[httpx.Request] = []
    client = fred(
        {
            "series/observations": {
                "observations": [
                    {"realtime_start": "2026-09-11", "date": "2026-08-01", "value": "0.4"},
                    {"realtime_start": "2026-10-15", "date": "2026-09-01", "value": "."},
                    {"realtime_start": "2024-01-01", "date": "2023-12-01", "value": "0.1"},
                    {"date": "2026-07-01", "value": "0.2"},
                ]
            }
        },
        seen,
    )
    rows = client.initial_releases("CPIAUCSL", "pch", date(2024, 9, 1))
    assert rows == [(T("2026-09-11"), T("2026-08-01"), 0.4)]
    params = seen[0].url.params
    assert params["output_type"] == "4" and params["units"] == "pch"
    assert params["api_key"] == "k" and params["file_type"] == "json"


def test_fred_observations_and_release_dates() -> None:
    seen: list[httpx.Request] = []
    client = fred(
        {
            "series/observations": {
                "observations": [
                    {"date": "2026-09-17", "value": "5.25"},
                    {"date": "2026-09-16", "value": "5.50"},
                    {"date": "2026-09-18", "value": "."},
                ]
            },
            "release/dates": {
                "release_dates": [
                    {"release_id": 101, "date": "2026-09-16"},
                    {"release_id": 101, "date": "2026-09-16"},
                    {"release_id": 101, "date": "bad"},
                    {"release_id": 101, "date": "2026-07-29"},
                ]
            },
        },
        seen,
    )
    upper = client.observations("DFEDTARU", date(2026, 1, 1))
    assert upper.index.tolist() == [T("2026-09-16"), T("2026-09-17")]
    assert client.release_dates(101, date(2026, 1, 1)) == [T("2026-07-29"), T("2026-09-16")]


def test_fred_unexpected_shapes_are_errors() -> None:
    client = fred({"series/observations": [], "release/dates": {}}, [])
    with pytest.raises(SourceError):
        client.observations("X", date(2026, 1, 1))
    with pytest.raises(SourceError):
        client.release_dates(101, date(2026, 1, 1))


# ---- service and job ----


class FakeFred:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def initial_releases(
        self, series_id: str, units: str, start: date
    ) -> list[tuple[pd.Timestamp, pd.Timestamp, float]]:
        if series_id in self.failing or "*" in self.failing:
            raise SourceUnavailable("down")
        return [(T("2026-10-15"), T("2026-09-01"), 0.3), (T("2026-09-11"), T("2026-08-01"), 0.2)]

    def observations(self, series_id: str, start: date) -> pd.Series:
        if "*" in self.failing:
            raise SourceUnavailable("down")
        return pd.Series([5.5, 5.25], index=[T("2026-09-16"), T("2026-09-17")])

    def release_dates(self, release_id: int, start: date) -> list[pd.Timestamp]:
        return [T("2026-09-16")]


def test_collect_reads_every_series_and_survives_one_failing() -> None:
    frame, report = collect(FakeFred({"CPIAUCSL"}), date(2024, 9, 1), date(2026, 11, 4))
    assert report.failed == ("cpi",)
    assert "cpi" not in set(frame["key"])
    assert set(frame["key"]) >= {FOMC, "core_cpi", "payrolls"}
    fomc = frame[frame["key"] == FOMC].iloc[0]
    assert (fomc["value"], fomc["previous"]) == (5.25, 5.5)


def test_merge_keeps_last_nights_rows_for_failed_series() -> None:
    stored = normalize_macro(
        pd.DataFrame(
            [
                ["2026-09-11", "cpi", "2026-08-01", 0.2, None],
                ["2026-09-11", "ppi", "2026-08-01", 9.9, None],
            ],
            columns=["release_date", "key", "period", "value", "previous"],
        )
    )
    fresh = normalize_macro(
        pd.DataFrame(
            [["2026-09-12", "ppi", "2026-08-01", 0.1, None]],
            columns=["release_date", "key", "period", "value", "previous"],
        )
    )
    merged = merge_macro(stored, fresh, {"cpi"})
    assert sorted(zip(merged["key"], merged["value"], strict=True)) == [("cpi", 0.2), ("ppi", 0.1)]


def test_store_round_trip_and_missing_file(tmp_path: Path) -> None:
    store = ParquetMacroStore(tmp_path)
    assert store.load().empty
    frame, _ = collect(FakeFred(), date(2024, 9, 1), date(2026, 11, 4))
    store.save(frame)
    pd.testing.assert_frame_equal(store.load(), frame)


def test_job_needs_a_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WHYMOVED_FRED_API_KEY", raising=False)
    assert collect_macro.main([]) == 2


@pytest.mark.parametrize(("failing", "code"), [(set(), 0), ({"CPIAUCSL"}, 1), ({"*"}, 1)])
def test_job_saves_what_it_could_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failing: set[str], code: int
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WHYMOVED_FRED_API_KEY", "k")
    monkeypatch.setattr(collect_macro, "fred_client", lambda _key: FakeFred(failing))
    assert collect_macro.main([]) == code
    path = tmp_path / "data" / "macro_releases.parquet"
    if "*" in failing:
        assert not path.exists()
    else:
        saved = ParquetMacroStore(tmp_path / "data").load()
        assert ("cpi" in set(saved["key"])) == (not failing)


def test_empty_calendar_has_the_schema() -> None:
    assert list(empty_macro().columns) == ["release_date", "key", "period", "value", "previous"]
    assert json.dumps(list(SERIES_BY_KEY))  # keys are plain strings
