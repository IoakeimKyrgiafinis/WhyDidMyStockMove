from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from market import NEWS_DAY, day_at, make_market
from peer_market import make_peer_snapshot
from test_reactions import provider_for
from whymoved.api.observability import JsonFormatter, request_id
from whymoved.api.ratelimit import RateLimited, TokenBucket
from whymoved.main import create_app
from whymoved.services.explain import MoveExplainer
from whymoved.services.reactions import ReactionService


@pytest.fixture(scope="module")
def client() -> TestClient:
    explainer = MoveExplainer(make_peer_snapshot(), n_boot=200)
    return TestClient(create_app(explainer, reactions=ReactionService(None)))


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    generated = client.get("/health").headers["x-request-id"]
    echoed = client.get("/health", headers={"X-Request-ID": "trace-12345678"})
    rejected = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})

    assert len(generated) == 16
    assert echoed.headers["x-request-id"] == "trace-12345678"
    assert rejected.headers["x-request-id"] != "bad id with spaces"


def test_cache_headers_follow_the_nightly_data(client: TestClient) -> None:
    assert client.get("/api/v1/stocks/AAA").headers["cache-control"] == "public, max-age=300"
    assert client.get("/api/v1/meta").headers["cache-control"] == "public, max-age=30"
    assert client.get("/api/v1/stocks/ZZZ").headers["cache-control"] == "no-store"
    assert "cache-control" not in client.get("/health").headers


def test_large_json_is_compressed_but_streams_are_not(client: TestClient) -> None:
    big = client.get("/api/v1/stocks/AAA/peers", headers={"Accept-Encoding": "gzip"})
    sse = client.get("/api/v1/stocks/AAA/peers/stream", headers={"Accept-Encoding": "gzip"})

    assert big.headers["content-encoding"] == "gzip"
    assert "content-encoding" not in sse.headers
    assert sse.headers["cache-control"] == "no-cache"


def test_json_log_lines_include_the_request_id() -> None:
    token = request_id.set("abc123def456")
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("you",), None)
        record.fields = {"status": 200}
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id.reset(token)

    assert line["message"] == "hello you"
    assert line["request_id"] == "abc123def456"
    assert line["status"] == 200


def test_token_bucket_allows_a_burst_then_refills() -> None:
    now = [0.0]
    bucket = TokenBucket(2, per_minute=60, clock=lambda: now[0])

    bucket.take("a")
    bucket.take("a")
    with pytest.raises(RateLimited) as info:
        bucket.take("a")
    assert info.value.retry_after == pytest.approx(1.0)
    bucket.take("b")  # other visitors are unaffected

    now[0] = 1.0
    bucket.take("a")


def test_the_reactions_endpoint_is_rate_limited() -> None:
    market = make_market(with_events=True)
    explainer = MoveExplainer(market.snapshot, n_boot=200)
    app = create_app(explainer, reactions=ReactionService(provider_for(market)))
    app.state.reaction_limiter = TokenBucket(1, per_minute=1)
    client = TestClient(app)
    url = f"/api/v1/stocks/AAA/days/{day_at(market, NEWS_DAY).isoformat()}/reactions"

    assert client.get(url).status_code == 200
    limited = client.get(url)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["retry-after"]) >= 1
