"""Unit tests for the in-process authentication failure limiter.

The API-level throttling behavior (429 responses, Retry-After, proxy trust)
is covered in test_auth.py; these tests exercise the limiter and the source
attribution directly, with an injected clock.
"""

from __future__ import annotations

from fastapi import Request

from cfm.ratelimit import AuthRateLimiter, client_source


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_limiter(
    clock: FakeClock,
    *,
    max_failures: int = 3,
    window_seconds: float = 60,
    lockout_seconds: float = 300,
    sweep_threshold: int = 1024,
) -> AuthRateLimiter:
    return AuthRateLimiter(
        max_failures=max_failures,
        window_seconds=window_seconds,
        lockout_seconds=lockout_seconds,
        clock=clock,
        sweep_threshold=sweep_threshold,
    )


def make_request(
    headers: list[tuple[bytes, bytes]],
    client: tuple[str, int] | None = ("192.0.2.10", 40000),
) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
        "client": client,
    }
    return Request(scope)


def test_locks_after_max_failures_within_window() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=3)
    assert limiter.retry_after("a") is None
    limiter.record_failure("a")
    limiter.record_failure("a")
    assert limiter.retry_after("a") is None
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 300


def test_failures_outside_the_window_do_not_count() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=3, window_seconds=60)
    limiter.record_failure("a")
    clock.advance(61)
    limiter.record_failure("a")
    clock.advance(61)
    limiter.record_failure("a")
    # Three failures, but never three within one 60-second window.
    assert limiter.retry_after("a") is None


def test_lockout_expires_and_the_failure_budget_restarts() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=3, lockout_seconds=300)
    for _ in range(3):
        limiter.record_failure("a")
    assert limiter.retry_after("a") == 300
    clock.advance(299)
    assert limiter.retry_after("a") == 1
    clock.advance(2)
    assert limiter.retry_after("a") is None
    # The budget is fresh after the lockout: two failures do not re-lock.
    limiter.record_failure("a")
    limiter.record_failure("a")
    assert limiter.retry_after("a") is None


def test_retry_after_rounds_up_to_whole_seconds() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=1, lockout_seconds=300)
    limiter.record_failure("a")
    clock.advance(0.5)
    assert limiter.retry_after("a") == 300


def test_sources_are_independent() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=3)
    for _ in range(3):
        limiter.record_failure("a")
    assert limiter.retry_after("a") == 300
    assert limiter.retry_after("b") is None
    limiter.record_failure("b")
    assert limiter.retry_after("b") is None


def test_zero_max_failures_disables_the_limiter() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=0)
    for _ in range(100):
        limiter.record_failure("a")
    assert limiter.retry_after("a") is None


def test_stale_sources_are_swept_to_bound_memory() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=3, window_seconds=60, sweep_threshold=10)
    for index in range(11):
        limiter.record_failure(f"source-{index}")
    clock.advance(61)
    # All previous failures are now outside the window; the next recording
    # sweeps the stale entries instead of growing without bound.
    limiter.record_failure("fresh")
    assert set(limiter._sources) == {"fresh"}


def test_locked_sources_survive_the_sweep() -> None:
    clock = FakeClock()
    limiter = make_limiter(
        clock, max_failures=1, window_seconds=60, lockout_seconds=300, sweep_threshold=5
    )
    limiter.record_failure("locked")
    for index in range(6):
        limiter.record_failure(f"source-{index}")
    clock.advance(61)
    limiter.record_failure("fresh")
    assert limiter.retry_after("locked") is not None


def test_client_source_uses_the_socket_peer_by_default() -> None:
    request = make_request([(b"x-forwarded-for", b"203.0.113.7")])
    assert client_source(request, trusted_proxy=False) == "192.0.2.10"


def test_client_source_without_a_peer_is_unknown() -> None:
    request = make_request([], client=None)
    assert client_source(request, trusted_proxy=False) == "unknown"


def test_trusted_proxy_takes_the_rightmost_forwarded_entry() -> None:
    request = make_request([(b"x-forwarded-for", b"198.51.100.1, 203.0.113.7")])
    assert client_source(request, trusted_proxy=True) == "203.0.113.7"


def test_trusted_proxy_joins_repeated_forwarded_headers() -> None:
    request = make_request(
        [
            (b"x-forwarded-for", b"198.51.100.1"),
            (b"x-forwarded-for", b"198.51.100.2, 203.0.113.9"),
        ]
    )
    assert client_source(request, trusted_proxy=True) == "203.0.113.9"


def test_trusted_proxy_falls_back_to_the_socket_without_a_header() -> None:
    request = make_request([])
    assert client_source(request, trusted_proxy=True) == "192.0.2.10"


def test_trusted_proxy_ignores_a_blank_forwarded_header() -> None:
    request = make_request([(b"x-forwarded-for", b"   ")])
    assert client_source(request, trusted_proxy=True) == "192.0.2.10"
