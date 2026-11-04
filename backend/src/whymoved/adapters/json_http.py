"""GET requests to public JSON services (FRED, GDELT, openFDA, ClinicalTrials.gov) with retries.

HTTP 429, 5xx and network errors are retried with backoff (Retry-After is honoured, capped); any
other error fails at once. Each client spaces its requests to stay inside the service's limit.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping

import httpx

from whymoved.adapters.http_policy import RateLimiter, RetryPolicy

logger = logging.getLogger(__name__)

MAX_RETRY_AFTER = 60.0


class SourceError(Exception):
    """A public data service could not answer."""


class SourceUnavailable(SourceError):
    """Network errors, rate limiting or server errors persisted through every retry."""


class SourceRequestError(SourceError):
    """The service refused the request (a bad key, a bad query)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class JsonHttp:
    def __init__(
        self,
        client: httpx.Client,
        *,
        name: str,
        min_interval: float = 0.0,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        redact: Mapping[str, str] | None = None,
    ) -> None:
        self._client = client
        self._name = name
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._limiter = RateLimiter(min_interval, clock=clock, sleep=sleep)
        # Secret values that must never appear in an error message (an API key in the URL).
        self._redact = dict(redact or {})

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JsonHttp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, url: str, params: Mapping[str, str]) -> object:
        """The decoded JSON body of a GET request."""
        attempt = 0
        while True:
            attempt += 1
            self._limiter.wait()
            try:
                response = self._client.get(url, params=dict(params))
            except httpx.TransportError as exc:
                if attempt >= self._retry.max_attempts:
                    raise SourceUnavailable(
                        f"{self._name}: network error after {attempt} attempts: "
                        f"{self._clean(str(exc))}"
                    ) from None
                self._sleep(self._retry.backoff(attempt))
                continue
            status = response.status_code
            if status == 200:
                try:
                    return response.json()
                except ValueError:
                    raise SourceError(f"{self._name}: the response was not JSON") from None
            message = self._clean(response.text[:200].strip() or response.reason_phrase)
            if status == 429 or status >= 500:
                if attempt >= self._retry.max_attempts:
                    raise SourceUnavailable(
                        f"{self._name}: HTTP {status} after {attempt} attempts: {message}"
                    )
                delay = self._delay(response, attempt)
                logger.warning(
                    "%s: HTTP %d, retrying in %.1fs (attempt %d)",
                    self._name,
                    status,
                    delay,
                    attempt,
                )
                self._sleep(delay)
                continue
            raise SourceRequestError(status, f"{self._name}: {message}")

    def _delay(self, response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("Retry-After")
        if raw is not None:
            try:
                return min(max(float(raw), 0.0), MAX_RETRY_AFTER)
            except ValueError:
                pass
        return self._retry.backoff(attempt)

    def _clean(self, text: str) -> str:
        for secret, label in self._redact.items():
            if secret:
                text = text.replace(secret, label)
        return text
