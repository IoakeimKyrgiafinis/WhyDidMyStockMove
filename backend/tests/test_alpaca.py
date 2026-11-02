from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest
from pydantic import SecretStr

from whymoved.adapters import alpaca
from whymoved.adapters.alpaca import (
    DATA_URL,
    AlpacaAuthError,
    AlpacaDailyBars,
    AlpacaError,
    AlpacaRequestError,
    AlpacaUnavailable,
    Feed,
    build_client,
    from_settings,
)
from whymoved.adapters.http_policy import RetryPolicy
from whymoved.config import JobSettings

START = date(2024, 1, 1)
END = date(2024, 1, 31)


def bar(day: str, close: float, volume: float = 1000.0) -> dict[str, object]:
    return {
        "t": f"{day}T04:00:00Z",
        "o": close,
        "h": close,
        "l": close,
        "c": close,
        "v": volume,
        "n": 10,
        "vw": close,
    }


def page(bars: dict[str, list[dict[str, object]]], token: str | None = None) -> dict[str, object]:
    return {"bars": bars, "next_page_token": token}


def ok(payload: object, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(200, json=payload, headers=headers)


def failure(
    status: int, message: str = "oops", headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, json={"message": message}, headers=headers)


@dataclass
class Harness:
    provider: AlpacaDailyBars
    requests: list[httpx.Request]
    sleeps: list[float]


def make_harness(
    script: Sequence[httpx.Response | Exception],
    *,
    feed: Feed = "iex",
    batch_size: int = 100,
    retry: RetryPolicy | None = None,
) -> Harness:
    """A provider whose HTTP calls are answered from `script`, in order."""
    queue = list(script)
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if not queue:
            raise AssertionError("unexpected extra request")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = build_client(
        SecretStr("key-123"),
        SecretStr("secret-456"),
        transport=httpx.MockTransport(handler),
    )
    provider = AlpacaDailyBars(
        client,
        feed=feed,
        batch_size=batch_size,
        retry=retry,
        sleep=sleeps.append,
        clock=lambda: 1000.0,
    )
    return Harness(provider=provider, requests=requests, sleeps=sleeps)


# ---- parsing ----


def test_bars_become_wide_close_and_volume_frames() -> None:
    harness = make_harness(
        [
            ok(
                page(
                    {
                        "AAA": [bar("2024-01-02", 10.0, 100.0), bar("2024-01-03", 11.0, 200.0)],
                        "BBB": [bar("2024-01-03", 20.0, 300.0)],
                    }
                )
            )
        ]
    )

    bars = harness.provider.daily_bars(["AAA", "BBB"], START, END)

    assert bars.symbols == ["AAA", "BBB"]
    assert bars.close.index.tolist() == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert isinstance(bars.close.index, pd.DatetimeIndex)
    assert bars.close.index.tz is None
    assert bars.close["AAA"].tolist() == [10.0, 11.0]
    assert bool(np.isnan(float(bars.close["BBB"].iloc[0])))
    assert float(bars.close["BBB"].iloc[1]) == 20.0
    assert bars.volume["AAA"].tolist() == [100.0, 200.0]


def test_symbols_without_bars_become_all_nan_columns() -> None:
    harness = make_harness([ok(page({"AAA": [bar("2024-01-02", 10.0)]}))])

    bars = harness.provider.daily_bars(["AAA", "ZZZ"], START, END)

    assert bars.symbols == ["AAA", "ZZZ"]
    assert bool(bars.close["ZZZ"].isna().all())
    assert bool(bars.volume["ZZZ"].isna().all())


def test_a_null_bars_field_means_no_data() -> None:
    harness = make_harness([ok({"bars": None, "next_page_token": None})])

    bars = harness.provider.daily_bars(["AAA"], START, END)

    assert bars.close.empty
    assert bars.symbols == ["AAA"]


def test_unrequested_symbols_are_ignored() -> None:
    harness = make_harness(
        [ok(page({"AAA": [bar("2024-01-02", 10.0)], "OLD": [bar("2024-01-02", 5.0)]}))]
    )

    bars = harness.provider.daily_bars(["AAA"], START, END)

    assert bars.symbols == ["AAA"]


# ---- requests ----


def test_request_parameters_and_credentials() -> None:
    harness = make_harness([ok(page({}))], feed="iex")

    harness.provider.daily_bars(["AAA", "BBB"], START, END)

    request = harness.requests[0]
    assert str(request.url).startswith(DATA_URL + "/v2/stocks/bars")
    assert dict(request.url.params) == {
        "symbols": "AAA,BBB",
        "timeframe": "1Day",
        "start": "2024-01-01",
        "end": "2024-01-31",
        "limit": "10000",
        "adjustment": "all",
        "feed": "iex",
    }
    assert request.headers["APCA-API-KEY-ID"] == "key-123"
    assert request.headers["APCA-API-SECRET-KEY"] == "secret-456"


def test_pagination_follows_tokens_and_merges_pages() -> None:
    harness = make_harness(
        [
            ok(page({"AAA": [bar("2024-01-02", 10.0)]}, token="t1")),
            ok(page({"BBB": [bar("2024-01-02", 20.0)]}, token=None)),
        ]
    )

    bars = harness.provider.daily_bars(["AAA", "BBB"], START, END)

    assert len(harness.requests) == 2
    assert "page_token" not in harness.requests[0].url.params
    assert harness.requests[1].url.params["page_token"] == "t1"
    assert float(bars.close["AAA"].iloc[0]) == 10.0
    assert float(bars.close["BBB"].iloc[0]) == 20.0


def test_symbols_are_split_into_batches() -> None:
    harness = make_harness(
        [
            ok(page({"A1": [bar("2024-01-02", 1.0)], "A2": [bar("2024-01-02", 2.0)]})),
            ok(page({"A3": [bar("2024-01-02", 3.0)], "A4": [bar("2024-01-02", 4.0)]})),
            ok(page({"A5": [bar("2024-01-02", 5.0)]})),
        ],
        batch_size=2,
    )

    bars = harness.provider.daily_bars(["A1", "A2", "A3", "A4", "A5"], START, END)

    assert [r.url.params["symbols"] for r in harness.requests] == ["A1,A2", "A3,A4", "A5"]
    assert bars.symbols == ["A1", "A2", "A3", "A4", "A5"]
    assert bars.close.iloc[0].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_from_settings_uses_the_configured_feed_and_credentials() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return ok(page({}))

    settings = JobSettings(
        alpaca_key_id="key-1",
        alpaca_secret_key="secret-2",
        sec_user_agent="Jane Doe jane@example.com",
        alpaca_feed="sip",
        _env_file=None,
    )
    provider = from_settings(settings, transport=httpx.MockTransport(handler))

    provider.daily_bars(["AAA"], START, END)

    assert seen[0].url.params["feed"] == "sip"
    assert seen[0].headers["APCA-API-KEY-ID"] == "key-1"
    assert seen[0].headers["APCA-API-SECRET-KEY"] == "secret-2"


def test_the_provider_closes_its_client() -> None:
    client = build_client(SecretStr("k"), SecretStr("s"))

    with AlpacaDailyBars(client):
        assert not client.is_closed

    assert client.is_closed


# ---- retries and rate limits ----


def test_429_waits_for_retry_after() -> None:
    harness = make_harness(
        [failure(429, headers={"Retry-After": "2"}), ok(page({"AAA": [bar("2024-01-02", 1.0)]}))]
    )

    bars = harness.provider.daily_bars(["AAA"], START, END)

    assert harness.sleeps == [2.0]
    assert len(harness.requests) == 2
    assert float(bars.close["AAA"].iloc[0]) == 1.0


def test_429_without_retry_after_waits_for_the_reset_time() -> None:
    harness = make_harness([failure(429, headers={"X-RateLimit-Reset": "1005"}), ok(page({}))])

    harness.provider.daily_bars(["AAA"], START, END)

    assert harness.sleeps == [5.5]


def test_server_errors_back_off_exponentially() -> None:
    harness = make_harness([failure(500), failure(502), ok(page({}))])

    harness.provider.daily_bars(["AAA"], START, END)

    assert harness.sleeps == [1.0, 2.0]
    assert len(harness.requests) == 3


def test_backoff_is_capped() -> None:
    policy = RetryPolicy(max_attempts=6, base_delay=10.0, max_delay=25.0)
    harness = make_harness([failure(500)] * 5 + [ok(page({}))], retry=policy)

    harness.provider.daily_bars(["AAA"], START, END)

    assert harness.sleeps == [10.0, 20.0, 25.0, 25.0, 25.0]


def test_it_gives_up_after_the_maximum_attempts() -> None:
    harness = make_harness([failure(500)] * 3, retry=RetryPolicy(max_attempts=3))

    with pytest.raises(AlpacaUnavailable, match="HTTP 500"):
        harness.provider.daily_bars(["AAA"], START, END)

    assert len(harness.requests) == 3
    assert harness.sleeps == [1.0, 2.0]


def test_network_errors_are_retried() -> None:
    harness = make_harness([httpx.ConnectError("boom"), ok(page({}))])

    harness.provider.daily_bars(["AAA"], START, END)

    assert harness.sleeps == [1.0]
    assert len(harness.requests) == 2


def test_a_persistent_network_error_is_reported_as_unavailable() -> None:
    harness = make_harness([httpx.ReadTimeout("slow")] * 2, retry=RetryPolicy(max_attempts=2))

    with pytest.raises(AlpacaUnavailable, match="network error"):
        harness.provider.daily_bars(["AAA"], START, END)


def test_an_exhausted_quota_pauses_before_the_next_page() -> None:
    harness = make_harness(
        [
            ok(
                page({"AAA": [bar("2024-01-02", 1.0)]}, token="t1"),
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1003"},
            ),
            ok(
                page({"BBB": [bar("2024-01-02", 2.0)]}),
                headers={"X-RateLimit-Remaining": "100", "X-RateLimit-Reset": "1060"},
            ),
        ]
    )

    harness.provider.daily_bars(["AAA", "BBB"], START, END)

    assert harness.sleeps == [3.5]


# ---- errors that must not be retried ----


def test_auth_errors_are_not_retried_and_carry_the_message() -> None:
    harness = make_harness([failure(403, "subscription does not permit querying recent SIP data")])

    with pytest.raises(AlpacaAuthError, match="subscription"):
        harness.provider.daily_bars(["AAA"], START, END)

    assert len(harness.requests) == 1
    assert harness.sleeps == []


def test_other_client_errors_are_not_retried() -> None:
    harness = make_harness([failure(400, "invalid timeframe")])

    with pytest.raises(AlpacaRequestError, match="invalid timeframe") as caught:
        harness.provider.daily_bars(["AAA"], START, END)

    assert caught.value.status_code == 400
    assert len(harness.requests) == 1


def test_error_messages_never_contain_credentials() -> None:
    harness = make_harness([failure(401, "forbidden")])

    with pytest.raises(AlpacaAuthError) as caught:
        harness.provider.daily_bars(["AAA"], START, END)

    assert "key-123" not in str(caught.value)
    assert "secret-456" not in str(caught.value)


# ---- malformed responses and pagination guards ----


def test_a_body_that_is_not_json_is_an_error() -> None:
    harness = make_harness([httpx.Response(200, content=b"<html>maintenance</html>")])

    with pytest.raises(AlpacaError, match="JSON"):
        harness.provider.daily_bars(["AAA"], START, END)


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "an", "object"],
        {"bars": ["not", "a", "mapping"], "next_page_token": None},
        {"bars": {"AAA": "not a list"}, "next_page_token": None},
        {"bars": {"AAA": ["not a bar"]}, "next_page_token": None},
        {"bars": {"AAA": [{"t": 5, "c": 1.0, "v": 1.0}]}, "next_page_token": None},
        {"bars": {"AAA": [{"t": "garbage", "c": 1.0, "v": 1.0}]}, "next_page_token": None},
        {
            "bars": {"AAA": [{"t": "2024-01-02T04:00:00Z", "c": "x", "v": 1.0}]},
            "next_page_token": None,
        },
        {"bars": {}, "next_page_token": 7},
    ],
)
def test_malformed_payloads_raise(payload: object) -> None:
    harness = make_harness([ok(payload)])

    with pytest.raises(AlpacaError):
        harness.provider.daily_bars(["AAA"], START, END)


def test_a_repeated_page_token_stops_the_loop() -> None:
    harness = make_harness(
        [
            ok(page({"AAA": [bar("2024-01-02", 1.0)]}, token="same")),
            ok(page({"AAA": [bar("2024-01-03", 1.0)]}, token="same")),
        ]
    )

    with pytest.raises(AlpacaError, match="did not advance"):
        harness.provider.daily_bars(["AAA"], START, END)


def test_the_page_cap_stops_runaway_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alpaca, "MAX_PAGES_PER_BATCH", 3)
    harness = make_harness([ok(page({}, token=f"t{k}")) for k in range(1, 4)])

    with pytest.raises(AlpacaError, match="too many pages"):
        harness.provider.daily_bars(["AAA"], START, END)


# ---- validation ----


def test_invalid_requests_are_rejected() -> None:
    provider = make_harness([]).provider

    with pytest.raises(ValueError, match="empty"):
        provider.daily_bars([], START, END)
    with pytest.raises(ValueError, match="unique"):
        provider.daily_bars(["AAA", "AAA"], START, END)
    with pytest.raises(ValueError, match="invalid symbols"):
        provider.daily_bars(["AAA,BBB"], START, END)
    with pytest.raises(ValueError, match="invalid symbols"):
        provider.daily_bars(["aaa"], START, END)
    with pytest.raises(ValueError, match="start"):
        provider.daily_bars(["AAA"], END, START)


def test_invalid_configuration_is_rejected() -> None:
    with httpx.Client() as client:
        with pytest.raises(ValueError, match="batch_size"):
            AlpacaDailyBars(client, batch_size=0)
        with pytest.raises(ValueError, match="page_limit"):
            AlpacaDailyBars(client, page_limit=10_001)
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="max_delay"):
        RetryPolicy(base_delay=5.0, max_delay=1.0)
