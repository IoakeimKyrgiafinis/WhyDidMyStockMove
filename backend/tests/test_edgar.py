from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx
import pytest

from whymoved.adapters.edgar import (
    ARCHIVES_URL,
    INSIDER_DATASET_BASES,
    SUBMISSIONS_URL,
    TICKERS_URL,
    EdgarClient,
    from_settings,
)
from whymoved.adapters.edgar_errors import (
    EdgarBlocked,
    EdgarFormatError,
    EdgarNotFound,
    EdgarUnavailable,
)
from whymoved.adapters.http_policy import RateLimiter, RetryPolicy
from whymoved.config import JobSettings
from whymoved.domain.filings import FilingRef

USER_AGENT = "Test Agent test@example.com"
ACME_URL = f"{SUBMISSIONS_URL}CIK0000003453.json"
PAGE_ONE = "CIK0000003453-submissions-001.json"
PAGE_TWO = "CIK0000003453-submissions-002.json"

MINIMAL_FORM4 = (
    '<?xml version="1.0"?><ownershipDocument><documentType>4</documentType>'
    "<issuer><issuerCik>0000003453</issuerCik><issuerName>ACME CORP</issuerName>"
    "<issuerTradingSymbol>ACME</issuerTradingSymbol></issuer>"
    "<nonDerivativeTable><nonDerivativeTransaction><transactionCoding>"
    "<transactionCode>P</transactionCode></transactionCoding></nonDerivativeTransaction>"
    "</nonDerivativeTable></ownershipDocument>"
)

Route = list[httpx.Response | Exception]


@dataclass
class Harness:
    client: EdgarClient
    requests: list[httpx.Request]
    sleeps: list[float]


def make_harness(
    routes: dict[str, Route],
    *,
    retry: RetryPolicy | None = None,
    min_interval: float = 0.0,
) -> Harness:
    """A client whose HTTP calls are answered from `routes`, in order, per URL."""
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        queue = routes.get(str(request.url))
        if not queue:
            raise AssertionError(f"unexpected request: {request.url}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    http = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT})
    client = EdgarClient(
        http,
        retry=retry,
        limiter=RateLimiter(min_interval, clock=lambda: 100.0, sleep=sleeps.append),
        sleep=sleeps.append,
    )
    return Harness(client=client, requests=requests, sleeps=sleeps)


def columns(rows: list[tuple[str, str, str, str, str]]) -> dict[str, list[object]]:
    """Rows are (accession, filing date, form, primary document, items)."""
    return {
        "accessionNumber": [row[0] for row in rows],
        "filingDate": [row[1] for row in rows],
        "reportDate": [row[1] for row in rows],
        "acceptanceDateTime": [f"{row[1]}T21:30:00.000Z" for row in rows],
        "form": [row[2] for row in rows],
        "primaryDocument": [row[3] for row in rows],
        "items": [row[4] for row in rows],
    }


def submissions(
    rows: list[tuple[str, str, str, str, str]], files: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {"cik": "3453", "filings": {"recent": columns(rows), "files": files or []}}


RECENT = [
    ("0000000001-26-000001", "2026-08-31", "4", "xslF345X05/form4.xml", ""),
    ("0000000001-26-000002", "2026-08-20", "8-K", "acme8k.htm", "2.02,9.01"),
    ("0000000001-26-000003", "2026-06-02", "4/A", "xslF345X05/form4a.xml", ""),
    ("0000000001-26-000004", "2026-06-01", "10-Q", "q.htm", ""),
    ("0000000001-25-000005", "2025-06-02", "4", "xslF345X05/old4.xml", ""),
]
FILES: list[dict[str, object]] = [
    {"name": PAGE_ONE, "filingCount": 3, "filingFrom": "2023-01-02", "filingTo": "2025-05-30"},
    {"name": PAGE_TWO, "filingCount": 9, "filingFrom": "2015-01-01", "filingTo": "2022-12-31"},
]
OLDER = [
    ("0000000001-25-000009", "2025-05-30", "4", "xslF345X05/x.xml", ""),
    ("0000000001-23-000008", "2023-03-01", "4", "y.xml", ""),
    ("0000000001-22-000007", "2022-12-30", "4", "z.xml", ""),
]


def ok_json(payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload)


# ---- ticker map and filing lists ----


def test_the_ticker_map_is_parsed() -> None:
    payload = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
    harness = make_harness({TICKERS_URL: [ok_json(payload)]})

    assert harness.client.ticker_map() == {"NVDA": 1045810}
    assert harness.requests[0].headers["user-agent"] == USER_AGENT


def test_recent_filings_are_filtered_by_form_and_date() -> None:
    harness = make_harness({ACME_URL: [ok_json(submissions(RECENT, FILES))]})

    refs = harness.client.recent_filings(3453, {"4", "4/A"}, date(2026, 1, 1))

    assert [ref.accession for ref in refs] == ["0000000001-26-000003", "0000000001-26-000001"]
    assert [str(r.url) for r in harness.requests] == [ACME_URL]  # no older pages needed


def test_older_pages_are_fetched_only_when_they_are_needed() -> None:
    harness = make_harness(
        {
            ACME_URL: [ok_json(submissions(RECENT, FILES))],
            f"{SUBMISSIONS_URL}{PAGE_ONE}": [ok_json(columns(OLDER))],
        }
    )

    refs = harness.client.recent_filings(3453, {"4", "4/A"}, date(2023, 1, 1))

    assert [str(r.url) for r in harness.requests] == [ACME_URL, f"{SUBMISSIONS_URL}{PAGE_ONE}"]
    assert [ref.filing_date for ref in refs] == [
        date(2023, 3, 1),
        date(2025, 5, 30),
        date(2025, 6, 2),
        date(2026, 6, 2),
        date(2026, 8, 31),
    ]


def test_an_invalid_cik_is_rejected() -> None:
    with pytest.raises(ValueError, match="cik"):
        make_harness({}).client.recent_filings(0, {"4"}, date(2026, 1, 1))


# ---- Form 4 documents ----


def test_the_form_4_is_fetched_from_the_raw_document_path() -> None:
    ref = FilingRef(
        cik=3453,
        accession="0001225208-26-007379",
        form="4",
        filing_date=date(2026, 8, 31),
        accepted_at=None,
        report_date=None,
        primary_document="xslF345X05/form4.xml",
    )
    url = f"{ARCHIVES_URL}3453/000122520826007379/form4.xml"
    harness = make_harness({url: [httpx.Response(200, content=MINIMAL_FORM4.encode("utf-8"))]})

    filing = harness.client.insider_filing(ref)

    assert [str(r.url) for r in harness.requests] == [url]
    assert filing.issuer_ticker == "ACME"
    assert filing.transactions[0].code == "P"


def test_only_form_4_references_are_accepted() -> None:
    ref = FilingRef(3453, "0000000001-26-000002", "8-K", date(2026, 8, 20), None, None, "a.htm")

    with pytest.raises(ValueError, match="Form 4"):
        make_harness({}).client.insider_filing(ref)


# ---- failures ----


def test_a_missing_document_is_reported() -> None:
    harness = make_harness({ACME_URL: [httpx.Response(404)]})

    with pytest.raises(EdgarNotFound):
        harness.client.recent_filings(3453, {"4"}, date(2026, 1, 1))


def test_a_403_is_reported_without_retrying() -> None:
    harness = make_harness({TICKERS_URL: [httpx.Response(403)]})

    with pytest.raises(EdgarBlocked, match="User-Agent"):
        harness.client.ticker_map()

    assert len(harness.requests) == 1
    assert harness.sleeps == []


def test_429_waits_for_retry_after() -> None:
    harness = make_harness(
        {TICKERS_URL: [httpx.Response(429, headers={"Retry-After": "2"}), ok_json({})]}
    )

    assert harness.client.ticker_map() == {}
    assert harness.sleeps == [2.0]


def test_server_errors_back_off_exponentially() -> None:
    harness = make_harness({TICKERS_URL: [httpx.Response(500), httpx.Response(502), ok_json({})]})

    harness.client.ticker_map()

    assert harness.sleeps == [1.0, 2.0]


def test_it_gives_up_after_the_maximum_attempts() -> None:
    harness = make_harness(
        {TICKERS_URL: [httpx.Response(500)] * 3}, retry=RetryPolicy(max_attempts=3)
    )

    with pytest.raises(EdgarUnavailable, match="HTTP 500"):
        harness.client.ticker_map()

    assert len(harness.requests) == 3
    assert harness.sleeps == [1.0, 2.0]


def test_network_errors_are_retried() -> None:
    harness = make_harness({TICKERS_URL: [httpx.ConnectError("boom"), ok_json({})]})

    harness.client.ticker_map()

    assert harness.sleeps == [1.0]


def test_a_body_that_is_not_json_is_a_format_error() -> None:
    harness = make_harness({TICKERS_URL: [httpx.Response(200, content=b"<html>down</html>")]})

    with pytest.raises(EdgarFormatError, match="JSON"):
        harness.client.ticker_map()


def test_requests_are_spaced_by_the_rate_limiter() -> None:
    harness = make_harness({TICKERS_URL: [ok_json({}), ok_json({})]}, min_interval=0.1)

    harness.client.ticker_map()
    harness.client.ticker_map()

    assert harness.sleeps == pytest.approx([0.1])


# ---- construction ----


def test_from_settings_sends_the_configured_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    settings = JobSettings(
        alpaca_key_id="key",
        alpaca_secret_key="secret",
        sec_user_agent="Jane Doe jane@example.com",
        _env_file=None,
    )

    with from_settings(settings, transport=httpx.MockTransport(handler)) as client:
        client.ticker_map()

    assert seen[0].headers["user-agent"] == "Jane Doe jane@example.com"


def test_invalid_configuration_is_rejected() -> None:
    with httpx.Client() as http, pytest.raises(ValueError, match="max_pages"):
        EdgarClient(http, max_pages=-1)


def test_the_insider_data_set_is_found_in_the_second_directory() -> None:
    name = "2025q4_form345.zip"
    first = f"{INSIDER_DATASET_BASES[0]}{name}"
    second = f"{INSIDER_DATASET_BASES[1]}{name}"
    harness = make_harness(
        {first: [httpx.Response(404)], second: [httpx.Response(200, content=b"zip bytes")]}
    )

    assert harness.client.insider_dataset(2025, 4) == b"zip bytes"
    assert [str(r.url) for r in harness.requests] == [first, second]


def test_an_unpublished_insider_data_set_is_none() -> None:
    name = "2026q4_form345.zip"
    routes: dict[str, Route] = {
        f"{base}{name}": [httpx.Response(404)] for base in INSIDER_DATASET_BASES
    }

    assert make_harness(routes).client.insider_dataset(2026, 4) is None


def test_invalid_insider_data_set_requests_are_rejected() -> None:
    client = make_harness({}).client

    with pytest.raises(ValueError, match="quarter"):
        client.insider_dataset(2026, 5)
    with pytest.raises(ValueError, match="quarter"):
        client.insider_dataset(2005, 1)


# ---- press releases ----

FOLDER = "https://www.sec.gov/Archives/edgar/data/7/000000712600001/"
INDEX = FOLDER + "0000007-12-600001-index.htm"
EXHIBIT = FOLDER + "ex991.htm"
INDEX_PAGE = (
    '<table><tr><td scope="row">2</td><td scope="row">Press release</td>'
    '<td scope="row"><a href="/Archives/edgar/data/7/000000712600001/ex991.htm">ex991.htm</a></td>'
    '<td scope="row">EX-99.1</td><td scope="row">900</td></tr></table>'
)
RELEASE_PAGE = (
    "<p>Acme Reports Record Fourth Quarter Results</p><p>BOSTON - Acme Corp. (NYSE: ACME) today "
    "reported record results for the fourth quarter, with revenue up 20% and earnings per share "
    "up 25% from a year earlier.</p>"
)


def test_a_press_release_is_read_from_the_filing_index() -> None:
    h = make_harness(
        {
            INDEX: [httpx.Response(200, text=INDEX_PAGE)],
            EXHIBIT: [httpx.Response(200, text=RELEASE_PAGE)],
        }
    )

    found = h.client.press_release(7, "0000007-12-600001")

    assert found is not None
    url, release = found
    assert url == EXHIBIT
    assert release is not None and release.title == "Acme Reports Record Fourth Quarter Results"


def test_no_exhibit_and_oversized_exhibits() -> None:
    bare = make_harness({INDEX: [httpx.Response(200, text="<table></table>")]})
    huge = make_harness(
        {
            INDEX: [httpx.Response(200, text=INDEX_PAGE)],
            EXHIBIT: [httpx.Response(200, content=b"x" * 3_000_001)],
        }
    )

    assert bare.client.press_release(7, "0000007-12-600001") is None
    assert huge.client.press_release(7, "0000007-12-600001") == (EXHIBIT, None)
    with pytest.raises(ValueError, match="positive"):
        bare.client.press_release(0, "x")
