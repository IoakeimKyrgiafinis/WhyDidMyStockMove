from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay < 0.0:
            raise ValueError("base_delay must be non-negative")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be at least base_delay")

    def backoff(self, attempt: int) -> float:
        """Delay after the given failed attempt (1-based): base, 2x, 4x, ... up to max_delay."""
        return min(self.max_delay, self.base_delay * 2.0 ** (attempt - 1))


class RateLimiter:
    """Keeps consecutive requests at least `min_interval` seconds apart."""

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval < 0.0:
            raise ValueError("min_interval must be non-negative")
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next_allowed is not None and now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self._min_interval
