from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping

import httpx

from whymoved.adapters.alpaca import (
    MAX_WAIT_SECONDS,
    RESET_MARGIN_SECONDS,
    AlpacaAuthError,
    AlpacaError,
    AlpacaRequestError,
    AlpacaUnavailable,
)
from whymoved.adapters.http_policy import RetryPolicy

logger = logging.getLogger(__name__)


def _header_float(response: httpx.Response, name: str) -> float | None:
    raw = response.headers.get(name)
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message:
            return message[:200]
    return response.text[:200] or response.reason_phrase


def _json_object(response: httpx.Response) -> Mapping[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AlpacaError("the response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise AlpacaError("unexpected response format")
    return payload


class AlpacaHttp:
    """GET requests to an Alpaca data endpoint with retries and rate-limit handling.

    HTTP 429, 5xx and network errors are retried with backoff. A 429 honours Retry-After, then
    X-RateLimit-Reset. If a successful response reports no quota left, the next request waits for
    the reset. Authentication and other 4xx errors fail at once with Alpaca's own message.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._clock = clock

    def close(self) -> None:
        self._client.close()

    def get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, object]:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.get(path, params=dict(params))
            except httpx.TransportError as exc:
                if attempt >= self._retry.max_attempts:
                    raise AlpacaUnavailable(
                        f"network error after {attempt} attempts: {exc}"
                    ) from exc
                self._sleep(self._retry.backoff(attempt))
                continue

            status = response.status_code
            if status == 200:
                self._throttle(response)
                return _json_object(response)

            message = _error_message(response)
            if status in (401, 403):
                raise AlpacaAuthError(f"HTTP {status}: {message}")
            if status == 429 or status >= 500:
                if attempt >= self._retry.max_attempts:
                    raise AlpacaUnavailable(f"HTTP {status} after {attempt} attempts: {message}")
                delay = self._retry_delay(response, attempt)
                logger.warning("HTTP %d, retrying in %.1fs (attempt %d)", status, delay, attempt)
                self._sleep(delay)
                continue
            raise AlpacaRequestError(status, message)

    def _until_reset(self, response: httpx.Response) -> float | None:
        reset = _header_float(response, "X-RateLimit-Reset")
        if reset is None:
            return None
        wait = max(reset - self._clock(), 0.0) + RESET_MARGIN_SECONDS
        return min(wait, MAX_WAIT_SECONDS)

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = _header_float(response, "Retry-After")
        if retry_after is not None:
            return min(max(retry_after, 0.0), MAX_WAIT_SECONDS)
        if response.status_code == 429:
            wait = self._until_reset(response)
            if wait is not None:
                return wait
        return self._retry.backoff(attempt)

    def _throttle(self, response: httpx.Response) -> None:
        remaining = _header_float(response, "X-RateLimit-Remaining")
        if remaining is None or remaining > 0.0:
            return
        wait = self._until_reset(response)
        if wait is not None:
            logger.info("rate limit reached, waiting %.1fs", wait)
            self._sleep(wait)
