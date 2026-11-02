"""A small in-memory rate limiter for endpoints that spend an upstream quota.

Intraday reactions call Alpaca, whose free plan allows about 200 requests a minute for the
whole account. One visitor clicking through days must not use it all up.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from fastapi import Request


class RateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"too many requests; try again in {retry_after:.0f} seconds")
        self.retry_after = retry_after


class TokenBucket:
    """`capacity` requests at once, refilled at `per_minute` a minute, per client."""

    def __init__(
        self,
        capacity: int,
        per_minute: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_clients: int = 10_000,
    ) -> None:
        if capacity < 1 or per_minute <= 0:
            raise ValueError("capacity and per_minute must be positive")
        self._capacity = float(capacity)
        self._rate = per_minute / 60.0
        self._clock = clock
        self._max_clients = max_clients
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, client: str) -> None:
        """Spend one token for `client`, or raise RateLimited."""
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(client, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._rate)
            if tokens < 1.0:
                self._buckets[client] = (tokens, now)
                raise RateLimited((1.0 - tokens) / self._rate)
            if len(self._buckets) >= self._max_clients and client not in self._buckets:
                self._buckets.clear()  # crude bound on memory; a reset only loosens limits
            self._buckets[client] = (tokens - 1.0, now)


def client_key(request: Request) -> str:
    """The visitor's address. Behind a host's proxy (Render) it is the first X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
