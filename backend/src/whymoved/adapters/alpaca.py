from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date
from typing import Literal, Self

import httpx
import numpy as np
import pandas as pd
from pydantic import SecretStr

from whymoved.adapters.http_policy import RetryPolicy
from whymoved.config import JobSettings
from whymoved.domain.bars import DailyBars

logger = logging.getLogger(__name__)

DATA_URL = "https://data.alpaca.markets"
BARS_PATH = "/v2/stocks/bars"
MAX_PAGE_LIMIT = 10_000
MAX_PAGES_PER_BATCH = 1_000
RESET_MARGIN_SECONDS = 0.5
MAX_WAIT_SECONDS = 90.0

Feed = Literal["iex", "sip"]
Adjustment = Literal["raw", "split", "dividend", "spin-off", "all"]

_SYMBOL = re.compile(r"[A-Z0-9.]{1,10}")


class AlpacaError(Exception):
    """Base class for Alpaca adapter failures."""


class AlpacaAuthError(AlpacaError):
    """Credentials rejected, or the plan does not allow the request (HTTP 401 or 403)."""


class AlpacaRequestError(AlpacaError):
    """The request itself was rejected (other 4xx). Retrying will not help."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code


class AlpacaUnavailable(AlpacaError):
    """Rate limiting, server errors or network failures persisted through every retry."""


def build_client(
    key_id: SecretStr,
    secret_key: SecretStr,
    *,
    base_url: str = DATA_URL,
    timeout: float = 30.0,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """An HTTP client that sends the Alpaca credentials with every request."""
    return httpx.Client(
        base_url=base_url,
        headers={
            "APCA-API-KEY-ID": key_id.get_secret_value(),
            "APCA-API-SECRET-KEY": secret_key.get_secret_value(),
            "accept": "application/json",
        },
        timeout=timeout,
        transport=transport,
    )


def from_settings(
    settings: JobSettings, *, transport: httpx.BaseTransport | None = None
) -> AlpacaDailyBars:
    """Build the provider from the job settings (credentials and feed)."""
    client = build_client(settings.alpaca_key_id, settings.alpaca_secret_key, transport=transport)
    return AlpacaDailyBars(client, feed=settings.alpaca_feed)


def _chunks(items: Sequence[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


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


def _mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise AlpacaError(f"unexpected {what} in the response")
    return value


def _json_object(response: httpx.Response) -> Mapping[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AlpacaError("the response was not valid JSON") from exc
    return _mapping(payload, "response")


def _bar_date(value: object) -> date:
    if not isinstance(value, str) or len(value) < 10:
        raise AlpacaError("a bar has no usable timestamp")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise AlpacaError(f"a bar has an invalid timestamp: {value[:30]!r}") from exc


def _number(value: object) -> float:
    if value is None:
        return float("nan")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AlpacaError("a bar has a non-numeric value")
    return float(value)


def _to_frame(data: Mapping[str, Mapping[date, float]], symbols: Sequence[str]) -> pd.DataFrame:
    days = sorted({day for series in data.values() for day in series})
    row = {day: i for i, day in enumerate(days)}
    matrix = np.full((len(days), len(symbols)), np.nan, dtype=np.float64)
    for j, symbol in enumerate(symbols):
        for day, value in data.get(symbol, {}).items():
            matrix[row[day], j] = value
    index = pd.DatetimeIndex([pd.Timestamp(day) for day in days], name="date")
    return pd.DataFrame(matrix, index=index, columns=list(symbols))


class AlpacaDailyBars:
    """Daily bars from Alpaca's historical stock bars endpoint.

    Requests are made in batches of symbols, each paginated until Alpaca stops returning
    a page token. Rate limiting, server errors and network failures are retried with
    backoff; authentication and request errors are not.
    """

    def __init__(
        self,
        client: httpx.Client,
        *,
        feed: Feed = "iex",
        adjustment: Adjustment = "all",
        batch_size: int = 100,
        page_limit: int = MAX_PAGE_LIMIT,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if not 1 <= page_limit <= MAX_PAGE_LIMIT:
            raise ValueError(f"page_limit must be between 1 and {MAX_PAGE_LIMIT}")
        self._client = client
        self._feed = feed
        self._adjustment = adjustment
        self._batch_size = batch_size
        self._page_limit = page_limit
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self._clock = clock

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def daily_bars(self, symbols: Sequence[str], start: date, end: date) -> DailyBars:
        names = list(symbols)
        self._validate(names, start, end)

        closes: dict[str, dict[date, float]] = {symbol: {} for symbol in names}
        volumes: dict[str, dict[date, float]] = {symbol: {} for symbol in names}
        batches = list(_chunks(names, self._batch_size))
        for number, batch in enumerate(batches, start=1):
            logger.info(
                "fetching bars: batch %d of %d (%d symbols)", number, len(batches), len(batch)
            )
            self._fetch_batch(batch, start, end, closes, volumes)

        return DailyBars(close=_to_frame(closes, names), volume=_to_frame(volumes, names))

    @staticmethod
    def _validate(names: list[str], start: date, end: date) -> None:
        if not names:
            raise ValueError("symbols must not be empty")
        if len(set(names)) != len(names):
            raise ValueError("symbols must be unique")
        bad = [s for s in names if _SYMBOL.fullmatch(s) is None]
        if bad:
            raise ValueError(f"invalid symbols: {bad[:5]}")
        if start > end:
            raise ValueError("start must not be after end")

    def _fetch_batch(
        self,
        batch: list[str],
        start: date,
        end: date,
        closes: dict[str, dict[date, float]],
        volumes: dict[str, dict[date, float]],
    ) -> None:
        params = {
            "symbols": ",".join(batch),
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": str(self._page_limit),
            "adjustment": self._adjustment,
            "feed": self._feed,
        }
        requested = set(batch)
        seen_tokens: set[str] = set()

        for _ in range(MAX_PAGES_PER_BATCH):
            body = self._get(params)
            self._collect(body, requested, closes, volumes)

            token = body.get("next_page_token")
            if token is None or token == "":
                return
            if not isinstance(token, str):
                raise AlpacaError("unexpected page token in the response")
            if token in seen_tokens:
                raise AlpacaError("pagination did not advance")
            seen_tokens.add(token)
            params["page_token"] = token

        raise AlpacaError(f"too many pages for one batch (more than {MAX_PAGES_PER_BATCH})")

    @staticmethod
    def _collect(
        body: Mapping[str, object],
        requested: set[str],
        closes: dict[str, dict[date, float]],
        volumes: dict[str, dict[date, float]],
    ) -> None:
        bars = body.get("bars")
        if bars is None:
            return
        for symbol, rows in _mapping(bars, "bars").items():
            if symbol not in requested:
                logger.warning("ignoring bars for a symbol that was not requested: %s", symbol)
                continue
            if rows is None:
                continue
            if not isinstance(rows, list):
                raise AlpacaError("unexpected bar list in the response")
            for row in rows:
                bar = _mapping(row, "bar")
                day = _bar_date(bar.get("t"))
                closes[symbol][day] = _number(bar.get("c"))
                volumes[symbol][day] = _number(bar.get("v"))

    def _get(self, params: Mapping[str, str]) -> Mapping[str, object]:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.get(BARS_PATH, params=dict(params))
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
        """If the quota is used up, wait for it to reset before the next request."""
        remaining = _header_float(response, "X-RateLimit-Remaining")
        if remaining is None or remaining > 0.0:
            return
        wait = self._until_reset(response)
        if wait is not None:
            logger.info("rate limit reached, waiting %.1fs", wait)
            self._sleep(wait)
