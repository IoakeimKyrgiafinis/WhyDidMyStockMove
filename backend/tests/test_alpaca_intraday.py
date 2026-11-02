from __future__ import annotations

from datetime import datetime

import httpx
import numpy as np
import pytest
from pydantic import SecretStr

from whymoved.adapters.alpaca import AlpacaAuthError, AlpacaError
from whymoved.adapters.alpaca_intraday import AlpacaIntradayBars, build_intraday
from whymoved.adapters.http_policy import RetryPolicy

START = datetime(2026, 10, 30, 13, 30)
END = datetime(2026, 10, 30, 20, 0)


def client_for(pages: list[httpx.Response], seen: list[httpx.Request]) -> AlpacaIntradayBars:
    queue = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    http = httpx.Client(base_url="https://data.test", transport=httpx.MockTransport(handler))
    return AlpacaIntradayBars(
        http, retry=RetryPolicy(max_attempts=2, base_delay=0.0), sleep=lambda _: None
    )


def bar(t: str, c: object) -> dict[str, object]:
    return {"t": t, "o": 1.0, "h": 1.0, "l": 1.0, "c": c, "v": 100}


def test_pages_are_merged_into_a_wide_frame() -> None:
    seen: list[httpx.Request] = []
    pages = [
        httpx.Response(
            200,
            json={
                "bars": {
                    "AAA": [bar("2026-10-30T13:30:00Z", 10.0), bar("2026-10-30T13:35:00Z", 10.5)],
                    "SPY": [bar("2026-10-30T13:30:00Z", 500.0)],
                },
                "next_page_token": "p2",
            },
        ),
        httpx.Response(
            200,
            json={"bars": {"SPY": [bar("2026-10-30T13:35:00Z", 501.0)]}, "next_page_token": None},
        ),
    ]
    with client_for(pages, seen) as provider:
        frame = provider.bars(["AAA", "SPY", "XLK"], START, END)

    assert list(frame.columns) == ["AAA", "SPY", "XLK"]
    assert frame.index.tolist() == [datetime(2026, 10, 30, 13, 30), datetime(2026, 10, 30, 13, 35)]
    assert frame["AAA"].tolist() == [10.0, 10.5]
    assert frame["SPY"].tolist() == [500.0, 501.0]
    assert np.isnan(frame["XLK"]).all()  # requested but never traded: kept as NaN
    params = seen[0].url.params
    assert (
        params["timeframe"] == "5Min"
        and params["feed"] == "iex"
        and params["adjustment"] == "split"
    )
    assert seen[1].url.params["page_token"] == "p2"


def test_malformed_bars_are_skipped() -> None:
    seen: list[httpx.Request] = []
    page = httpx.Response(
        200,
        json={
            "bars": {
                "AAA": [
                    bar("2026-10-30T13:30:00Z", 10.0),
                    bar("not a time", 10.0),
                    bar("2026-10-30T13:40:00Z", -1.0),
                    bar("2026-10-30T13:45:00Z", True),
                    "junk",
                ]
            }
        },
    )
    frame = client_for([page], seen).bars(["AAA"], START, END)

    assert frame["AAA"].tolist() == [10.0]


def test_errors() -> None:
    seen: list[httpx.Request] = []
    with pytest.raises(AlpacaAuthError):
        client_for([httpx.Response(403, json={"message": "forbidden"})], seen).bars(
            ["AAA"], START, END
        )
    loop = httpx.Response(200, json={"bars": {}, "next_page_token": "same"})
    with pytest.raises(AlpacaError, match="pagination"):
        client_for([loop, loop], seen).bars(["AAA"], START, END)
    with pytest.raises(ValueError, match="empty"):
        client_for([], seen).bars([], START, END)
    with pytest.raises(ValueError, match="after"):
        client_for([], seen).bars(["AAA"], END, START)


def test_build_intraday_sends_credentials() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"bars": {}})

    provider = build_intraday(
        SecretStr("id"), SecretStr("secret"), transport=httpx.MockTransport(handler)
    )
    provider.bars(["AAA"], START, END)

    assert seen[0].headers["APCA-API-KEY-ID"] == "id"
    assert seen[0].url.path == "/v2/stocks/bars"
