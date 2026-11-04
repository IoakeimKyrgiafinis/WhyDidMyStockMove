"""The FRED API of the St. Louis Fed: release dates and first-published values.

A free key is required (fred.stlouisfed.org/docs/api/api_key.html). The limit is 120 requests a
minute; a nightly run makes about a dozen.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import httpx
import pandas as pd

from whymoved.adapters.json_http import JsonHttp, SourceError

BASE_URL = "https://api.stlouisfed.org/fred/"
MIN_INTERVAL = 0.6
TIMEOUT = 30.0
INITIAL_RELEASE = "4"  # output_type: each observation as first published, with that date


def _observations(payload: object) -> list[Mapping[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
        raise SourceError("FRED: unexpected observations response")
    return [o for o in payload["observations"] if isinstance(o, dict)]


def _number(raw: object) -> float | None:
    try:
        return float(str(raw))
    except ValueError:
        return None  # FRED writes "." for a missing value


class FredClient:
    def __init__(self, http: JsonHttp, api_key: str) -> None:
        self._http = http
        self._key = api_key

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> FredClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str, params: Mapping[str, str]) -> object:
        return self._http.get(
            BASE_URL + path, {**params, "api_key": self._key, "file_type": "json"}
        )

    def initial_releases(
        self, series_id: str, units: str, start: date
    ) -> list[tuple[pd.Timestamp, pd.Timestamp, float]]:
        """(first published, period, value) for each observation of a series from `start`."""
        payload = self._get(
            "series/observations",
            {
                "series_id": series_id,
                "units": units,
                "output_type": INITIAL_RELEASE,
                "realtime_start": start.isoformat(),
                "realtime_end": "9999-12-31",
                "observation_start": start.isoformat(),
            },
        )
        out: list[tuple[pd.Timestamp, pd.Timestamp, float]] = []
        for o in _observations(payload):
            value = _number(o.get("value"))
            if value is None:
                continue
            try:
                released = pd.Timestamp(str(o["realtime_start"]))
                period = pd.Timestamp(str(o["date"]))
            except (KeyError, ValueError):
                continue
            if released.date() < start:
                continue  # published before the window: the release day is not known
            out.append((released, period, value))
        return out

    def observations(self, series_id: str, start: date) -> pd.Series:
        """The current values of a series from `start`, by date."""
        payload = self._get(
            "series/observations", {"series_id": series_id, "observation_start": start.isoformat()}
        )
        values: dict[pd.Timestamp, float] = {}
        for o in _observations(payload):
            value = _number(o.get("value"))
            if value is not None and isinstance(o.get("date"), str):
                values[pd.Timestamp(str(o["date"]))] = value
        return pd.Series(values, dtype="float64").sort_index()

    def release_dates(self, release_id: int, start: date) -> list[pd.Timestamp]:
        """Every date a release was (or is scheduled to be) published from `start`."""
        payload = self._get(
            "release/dates",
            {
                "release_id": str(release_id),
                "realtime_start": start.isoformat(),
                "realtime_end": "9999-12-31",
                "include_release_dates_with_no_data": "true",
                "limit": "10000",
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("release_dates"), list):
            raise SourceError("FRED: unexpected release dates response")
        out: list[pd.Timestamp] = []
        for r in payload["release_dates"]:
            if isinstance(r, dict) and isinstance(r.get("date"), str):
                try:
                    out.append(pd.Timestamp(r["date"]))
                except ValueError:
                    continue
        return sorted(set(out))


def fred_client(api_key: str) -> FredClient:
    http = JsonHttp(
        httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "whystockmoved"}),
        name="FRED",
        min_interval=MIN_INTERVAL,
        redact={api_key: "<key>"},
    )
    return FredClient(http, api_key)
