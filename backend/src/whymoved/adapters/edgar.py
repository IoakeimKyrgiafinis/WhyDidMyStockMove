from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Collection
from datetime import date
from typing import Self

import httpx

from whymoved.adapters.edgar_errors import (
    EdgarBlocked,
    EdgarError,
    EdgarFormatError,
    EdgarNotFound,
    EdgarUnavailable,
)
from whymoved.adapters.edgar_parse import (
    as_mapping,
    parse_company_tickers,
    parse_filing_columns,
    parse_form4,
    parse_submissions,
    raw_xml_document,
)
from whymoved.adapters.http_policy import RateLimiter, RetryPolicy
from whymoved.config import JobSettings
from whymoved.domain.filings import FilingRef, InsiderFiling

logger = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
INSIDER_DATASET_BASES = (
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/",
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/",
)
MIN_INTERVAL_SECONDS = 0.12  # about 8 requests a second, under the SEC's limit of 10
MAX_WAIT_SECONDS = 90.0
FORM4_TYPES = frozenset({"4", "4/A"})


def from_settings(
    settings: JobSettings, *, transport: httpx.BaseTransport | None = None
) -> EdgarClient:
    """Build the client; every request carries the configured contact User-Agent."""
    http = httpx.Client(
        headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=30.0,
        transport=transport,
        follow_redirects=True,
    )
    return EdgarClient(http)


def _header_seconds(response: httpx.Response, name: str) -> float | None:
    raw = response.headers.get(name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


class EdgarClient:
    """SEC EDGAR access: ticker map, filing lists and Form 4 documents."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        retry: RetryPolicy | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_pages: int = 50,
    ) -> None:
        if max_pages < 0:
            raise ValueError("max_pages must be non-negative")
        self._client = client
        self._retry = retry or RetryPolicy()
        self._limiter = limiter or RateLimiter(MIN_INTERVAL_SECONDS)
        self._sleep = sleep
        self._max_pages = max_pages

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- public API ----

    def ticker_map(self) -> dict[str, int]:
        """Ticker (our notation, BRK.B) to CIK for every listed company."""
        return parse_company_tickers(self._json(TICKERS_URL))

    def recent_filings(self, cik: int, forms: Collection[str], since: date) -> list[FilingRef]:
        """Filings of the given form types filed on or after `since`, oldest first.

        The main submissions response holds roughly the latest 1,000 filings. Older pages
        are fetched only when `since` reaches back beyond them.
        """
        if cik < 1:
            raise ValueError("cik must be positive")
        wanted = set(forms)

        refs, pages = parse_submissions(self._json(f"{SUBMISSIONS_URL}CIK{cik:010d}.json"), cik)
        found = list(refs)
        covered_from = min((ref.filing_date for ref in refs), default=date.max)
        if covered_from > since:
            newest_first = sorted(pages, key=lambda page: page.filing_to, reverse=True)
            for page in newest_first[: self._max_pages]:
                if page.filing_to < since:
                    continue
                payload = self._json(f"{SUBMISSIONS_URL}{page.name}")
                found.extend(parse_filing_columns(as_mapping(payload, "older page"), cik))

        unique = {
            ref.accession: ref for ref in found if ref.form in wanted and ref.filing_date >= since
        }
        return sorted(unique.values(), key=lambda ref: (ref.filing_date, ref.accession))

    def insider_filing(self, ref: FilingRef) -> InsiderFiling:
        """Download and parse one Form 4 (or 4/A)."""
        if ref.form not in FORM4_TYPES:
            raise ValueError("insider_filing needs a Form 4 reference")
        document = raw_xml_document(ref.primary_document)
        if not document:
            raise EdgarFormatError("the filing has no primary document")
        url = f"{ARCHIVES_URL}{ref.cik}/{ref.accession.replace('-', '')}/{document}"
        return parse_form4(self._get(url).content, ref)

    def insider_dataset(self, year: int, quarter: int) -> bytes | None:
        """The raw ZIP of the SEC's quarterly insider transactions data set.

        The SEC moved newer quarters to a different directory, so both are tried. Returns None
        if the quarter is not (yet) published.
        """
        if not 1 <= quarter <= 4 or year < 2006:
            raise ValueError("quarter must be 1 to 4 and year 2006 or later")
        name = f"{year}q{quarter}_form345.zip"
        for base in INSIDER_DATASET_BASES:
            try:
                return self._get(base + name).content
            except EdgarNotFound:
                continue
        return None

    # ---- HTTP ----

    def _json(self, url: str) -> object:
        response = self._get(url)
        try:
            return response.json()
        except ValueError as exc:
            raise EdgarFormatError("the response was not valid JSON") from exc

    def _get(self, url: str) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            self._limiter.wait()
            try:
                response = self._client.get(url)
            except httpx.TransportError as exc:
                if attempt >= self._retry.max_attempts:
                    raise EdgarUnavailable(
                        f"network error after {attempt} attempts: {exc}"
                    ) from exc
                self._sleep(self._retry.backoff(attempt))
                continue

            status = response.status_code
            if status == 200:
                return response
            if status == 404:
                raise EdgarNotFound(f"not found: {url}")
            if status == 403:
                raise EdgarBlocked(
                    "the SEC refused the request (HTTP 403). Check the User-Agent contact "
                    "string. The SEC also blocks clients that exceed 10 requests a second, "
                    "so wait a few minutes before retrying."
                )
            if status == 429 or status >= 500:
                if attempt >= self._retry.max_attempts:
                    raise EdgarUnavailable(f"HTTP {status} after {attempt} attempts: {url}")
                delay = self._retry_delay(response, attempt)
                logger.warning("HTTP %d, retrying in %.1fs (attempt %d)", status, delay, attempt)
                self._sleep(delay)
                continue
            raise EdgarError(f"HTTP {status} for {url}")

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        wait = _header_seconds(response, "Retry-After")
        if wait is not None:
            return min(max(wait, 0.0), MAX_WAIT_SECONDS)
        return self._retry.backoff(attempt)
