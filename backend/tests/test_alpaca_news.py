from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from whymoved.adapters.alpaca import (
    AlpacaAuthError,
    AlpacaError,
    AlpacaRequestError,
    AlpacaUnavailable,
    build_client,
)
from whymoved.adapters.alpaca_news import AlpacaNews
from whymoved.adapters.http_policy import RetryPolicy

START = datetime(2026, 10, 28)
END = datetime(2026, 10, 30, 12, 0)


def article(
    id_: int,
    headline: str = "Headline",
    created: str = "2026-10-29T14:30:00Z",
    updated: str | None = None,
    symbols: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": id_,
        "headline": headline,
        "author": "Someone",
        "created_at": created,
        "updated_at": updated or created,
        "summary": "summary text",
        "content": "full text",
        "images": [],
        "url": f"https://example.com/{id_}",
        "symbols": symbols if symbols is not None else ["AAA"],
        "source": "benzinga",
    }


def page(articles: object, token: str | None = None) -> dict[str, object]:
    return {"news": articles, "next_page_token": token}


def ok(payload: object, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(200, json=payload, headers=headers)


@dataclass
class Harness:
    provider: AlpacaNews
    requests: list[httpx.Request]
    sleeps: list[float]


def make_harness(
    script: Sequence[httpx.Response | Exception],
    *,
    batch_size: int = 50,
    max_pages: int = 500,
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
        SecretStr("key-123"), SecretStr("secret-456"), transport=httpx.MockTransport(handler)
    )
    provider = AlpacaNews(
        client,
        batch_size=batch_size,
        max_pages=max_pages,
        retry=retry,
        sleep=sleeps.append,
        clock=lambda: 1000.0,
    )
    return Harness(provider=provider, requests=requests, sleeps=sleeps)


# ---- parsing ----


def test_articles_become_news_items_without_the_article_text() -> None:
    raw = article(
        1,
        "  NVDA jumps  ",
        created="2026-10-29T14:30:00Z",
        updated="2026-10-29T15:00:00Z",
        symbols=["NVDA", "BTC/USD", "NVDA", "BRK-B"],
    )
    harness = make_harness([ok(page([raw]))])

    (item,) = harness.provider.news(["NVDA"], START, END)

    assert item.id == 1
    assert item.headline == "NVDA jumps"
    assert item.source == "benzinga"
    assert item.url == "https://example.com/1"
    assert item.created_at == datetime(2026, 10, 29, 14, 30)
    assert item.updated_at == datetime(2026, 10, 29, 15, 0)
    assert item.symbols == ("NVDA", "BRK.B")
    assert not hasattr(item, "content")
    assert not hasattr(item, "summary")


def test_timestamps_with_offsets_become_naive_utc() -> None:
    raw = article(1, created="2026-10-29T10:30:00-04:00")
    harness = make_harness([ok(page([raw]))])

    (item,) = harness.provider.news(["AAA"], START, END)

    assert item.created_at == datetime(2026, 10, 29, 14, 30)
    assert item.created_at.tzinfo is None


def test_missing_optional_fields_get_defaults() -> None:
    raw = {k: v for k, v in article(1).items() if k not in ("url", "source")}
    raw["updated_at"] = "garbage"
    harness = make_harness([ok(page([raw]))])

    (item,) = harness.provider.news(["AAA"], START, END)

    assert item.url == "" and item.source == ""
    assert item.updated_at is None
    assert item.last_touched == item.created_at


def test_malformed_articles_are_skipped() -> None:
    articles = [
        article(1),
        "not a dict",
        {**article(2), "id": "x"},
        {**article(3), "id": True},
        {**article(4), "headline": "   "},
        {**article(5), "created_at": "yesterday"},
        {**article(6), "created_at": None},
    ]
    harness = make_harness([ok(page(articles))])

    items = harness.provider.news(["AAA"], START, END)

    assert [item.id for item in items] == [1]


def test_a_null_news_field_means_no_articles() -> None:
    harness = make_harness([ok(page(None))])

    assert harness.provider.news(["AAA"], START, END) == []


# ---- requests ----


def test_request_parameters_and_credentials() -> None:
    harness = make_harness([ok(page([]))])

    harness.provider.news(["NVDA", "AAPL"], START, END)

    request = harness.requests[0]
    assert request.url.path == "/v1beta1/news"
    assert dict(request.url.params) == {
        "symbols": "NVDA,AAPL",
        "start": "2026-10-28T00:00:00Z",
        "end": "2026-10-30T12:00:00Z",
        "limit": "50",
        "sort": "asc",
        "include_content": "false",
    }
    assert request.headers["APCA-API-KEY-ID"] == "key-123"
    assert request.headers["APCA-API-SECRET-KEY"] == "secret-456"


def test_pagination_follows_tokens_and_merges_pages() -> None:
    harness = make_harness(
        [
            ok(page([article(1, created="2026-10-29T10:00:00Z")], token="t1")),
            ok(page([article(2, created="2026-10-29T11:00:00Z")])),
        ]
    )

    items = harness.provider.news(["AAA"], START, END)

    assert [item.id for item in items] == [1, 2]
    assert "page_token" not in harness.requests[0].url.params
    assert harness.requests[1].url.params["page_token"] == "t1"


def test_a_repeated_page_token_stops_the_loop() -> None:
    harness = make_harness(
        [ok(page([article(1)], token="same")), ok(page([article(2)], token="same"))]
    )

    with pytest.raises(AlpacaError, match="did not advance"):
        harness.provider.news(["AAA"], START, END)


def test_the_page_cap_stops_runaway_pagination() -> None:
    harness = make_harness([ok(page([], token=f"t{k}")) for k in range(1, 4)], max_pages=3)

    with pytest.raises(AlpacaError, match="too many pages"):
        harness.provider.news(["AAA"], START, END)


def test_symbols_are_split_into_batches() -> None:
    harness = make_harness(
        [ok(page([article(1, symbols=["A1"])])), ok(page([article(2, symbols=["A3"])]))],
        batch_size=2,
    )

    harness.provider.news(["A1", "A2", "A3"], START, END)

    assert [r.url.params["symbols"] for r in harness.requests] == ["A1,A2", "A3"]


def test_duplicates_across_batches_keep_the_latest_update() -> None:
    first = article(1, "Old headline", updated="2026-10-29T15:00:00Z")
    second = article(2, "Second", created="2026-10-29T16:00:00Z")
    newer = article(1, "New headline", updated="2026-10-29T18:00:00Z")
    harness = make_harness([ok(page([first, second])), ok(page([newer]))], batch_size=2)

    items = harness.provider.news(["A1", "A2", "A3"], START, END)

    assert [item.id for item in items] == [1, 2]
    assert items[0].headline == "New headline"


# ---- failures ----


def test_auth_errors_carry_the_message_and_are_not_retried() -> None:
    harness = make_harness(
        [httpx.Response(403, json={"message": "news is not permitted on this plan"})]
    )

    with pytest.raises(AlpacaAuthError, match="not permitted"):
        harness.provider.news(["AAA"], START, END)

    assert len(harness.requests) == 1
    assert harness.sleeps == []


def test_other_client_errors_are_not_retried() -> None:
    harness = make_harness([httpx.Response(400, json={"message": "invalid sort"})])

    with pytest.raises(AlpacaRequestError, match="invalid sort") as caught:
        harness.provider.news(["AAA"], START, END)

    assert caught.value.status_code == 400
    assert len(harness.requests) == 1


def test_429_waits_for_retry_after() -> None:
    harness = make_harness(
        [httpx.Response(429, headers={"Retry-After": "2"}), ok(page([article(1)]))]
    )

    items = harness.provider.news(["AAA"], START, END)

    assert harness.sleeps == [2.0]
    assert len(items) == 1


def test_server_errors_back_off_exponentially() -> None:
    harness = make_harness([httpx.Response(500), httpx.Response(502), ok(page([]))])

    harness.provider.news(["AAA"], START, END)

    assert harness.sleeps == [1.0, 2.0]


def test_it_gives_up_after_the_maximum_attempts() -> None:
    harness = make_harness([httpx.Response(500)] * 3, retry=RetryPolicy(max_attempts=3))

    with pytest.raises(AlpacaUnavailable, match="HTTP 500"):
        harness.provider.news(["AAA"], START, END)

    assert len(harness.requests) == 3
    assert harness.sleeps == [1.0, 2.0]


def test_network_errors_are_retried() -> None:
    harness = make_harness([httpx.ConnectError("boom"), ok(page([]))])

    harness.provider.news(["AAA"], START, END)

    assert harness.sleeps == [1.0]


def test_an_exhausted_quota_pauses_before_the_next_page() -> None:
    harness = make_harness(
        [
            ok(
                page([article(1)], token="t1"),
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1003"},
            ),
            ok(page([article(2)])),
        ]
    )

    harness.provider.news(["AAA"], START, END)

    assert harness.sleeps == [3.5]


def test_a_body_that_is_not_json_is_an_error() -> None:
    harness = make_harness([httpx.Response(200, content=b"<html>maintenance</html>")])

    with pytest.raises(AlpacaError, match="JSON"):
        harness.provider.news(["AAA"], START, END)


@pytest.mark.parametrize(
    "payload",
    [["not", "an", "object"], {"news": "not a list"}, {"news": [], "next_page_token": 7}],
)
def test_malformed_payloads_raise(payload: object) -> None:
    harness = make_harness([ok(payload)])

    with pytest.raises(AlpacaError):
        harness.provider.news(["AAA"], START, END)


# ---- validation ----


def test_invalid_requests_are_rejected() -> None:
    provider = make_harness([]).provider
    aware = datetime(2026, 10, 28, tzinfo=UTC)

    with pytest.raises(ValueError, match="empty"):
        provider.news([], START, END)
    with pytest.raises(ValueError, match="unique"):
        provider.news(["AAA", "AAA"], START, END)
    with pytest.raises(ValueError, match="invalid symbols"):
        provider.news(["aaa"], START, END)
    with pytest.raises(ValueError, match="naive"):
        provider.news(["AAA"], aware, END)
    with pytest.raises(ValueError, match="start"):
        provider.news(["AAA"], END, START)


def test_invalid_configuration_is_rejected() -> None:
    with httpx.Client() as client:
        with pytest.raises(ValueError, match="batch_size"):
            AlpacaNews(client, batch_size=0)
        with pytest.raises(ValueError, match="max_pages"):
            AlpacaNews(client, max_pages=0)
