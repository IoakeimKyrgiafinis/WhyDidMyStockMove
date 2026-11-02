from __future__ import annotations

import pytest

from whymoved.adapters.http_policy import RateLimiter, RetryPolicy


def test_backoff_doubles_and_is_capped() -> None:
    policy = RetryPolicy(base_delay=1.0, max_delay=5.0)

    assert [policy.backoff(k) for k in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 5.0]


def test_invalid_policies_are_rejected() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="base_delay"):
        RetryPolicy(base_delay=-1.0)
    with pytest.raises(ValueError, match="max_delay"):
        RetryPolicy(base_delay=5.0, max_delay=1.0)


def test_requests_are_spaced_by_the_minimum_interval() -> None:
    sleeps: list[float] = []
    limiter = RateLimiter(0.1, clock=lambda: 100.0, sleep=sleeps.append)

    for _ in range(3):
        limiter.wait()

    assert sleeps == pytest.approx([0.1, 0.2])


def test_there_is_no_wait_when_enough_time_has_passed() -> None:
    sleeps: list[float] = []
    times = iter([0.0, 0.05, 0.5])
    limiter = RateLimiter(0.1, clock=lambda: next(times), sleep=sleeps.append)

    for _ in range(3):
        limiter.wait()

    assert sleeps == pytest.approx([0.05])


def test_a_negative_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="min_interval"):
        RateLimiter(-0.1)
