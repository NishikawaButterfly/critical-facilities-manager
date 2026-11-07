"""Unit tests for the in-process authentication failure limiter.

The API-level throttling behavior (429 responses, Retry-After, proxy trust)
is covered in test_auth.py; these tests exercise the limiter and the source
attribution directly, with an injected clock.
"""

from __future__ import annotations

import pytest
from fastapi import Request

from cfm.ratelimit import AuthRateLimiter


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


# --- One failure, several sources ------------------------------------------


def test_a_failure_counts_against_every_source_it_names() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=3)
    limiter.record_failures({"a", "b"})
    limiter.record_failures({"a", "b"})
    limiter.record_failures({"a"})
    # Each source keeps its own count: three failures named "a", two "b".
    assert limiter.retry_after("a") == 300
    assert limiter.retry_after("b") is None
    limiter.record_failures({"b"})
    assert limiter.retry_after("b") == 300


def test_an_attempt_is_refused_while_any_of_its_sources_is_locked() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=3)
    for _ in range(3):
        limiter.record_failures({"a"})
    assert limiter.retry_after_any({"a", "b"}) == 300
    assert limiter.retry_after_any({"b", "c"}) is None


def test_a_source_named_twice_counts_once() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=2)
    limiter.record_failures(["a", "a"])
    assert limiter.retry_after("a") is None
    limiter.record_failures(["a"])
    assert limiter.retry_after("a") == 300


def test_retry_after_any_is_the_longest_remaining_lockout() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=1, lockout_seconds=300)
    limiter.record_failures({"a"})
    clock.advance(100)
    limiter.record_failures({"b"})
    # "a" has 200 seconds left and "b" 300: an attempt that counts against
    # both waits for both, whichever order they are named in.
    assert limiter.retry_after_any(("a", "b")) == 300
    assert limiter.retry_after_any(("b", "a")) == 300
    clock.advance(150)
    assert limiter.retry_after_any(("a", "b")) == 150
    clock.advance(50)
    assert limiter.retry_after("a") is None
    assert limiter.retry_after_any(("a", "b")) == 100


def test_waiting_the_seconds_reported_frees_every_source() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=1, lockout_seconds=300)
    limiter.record_failures({"a"})
    clock.advance(0.5)
    limiter.record_failures({"b"})
    clock.advance(0.3)
    retry_after = limiter.retry_after_any({"a", "b"})
    # 299.7 seconds remain on "b". Rounded up rather than down, so a client
    # that waits exactly as long as it was told finds every source free.
    assert retry_after == 300
    clock.advance(retry_after)
    assert limiter.retry_after_any({"a", "b"}) is None


def test_a_lockout_is_never_extended() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=3, lockout_seconds=300)
    for _ in range(3):
        limiter.record_failure("a")
    clock.advance(100)
    # A failure reaches a locked source only from an attempt that was
    # checked before the lock was set, one still in flight at that moment.
    # However many arrive, the lockout ends when it was going to.
    for _ in range(3):
        limiter.record_failure("a")
    assert limiter.retry_after("a") == 200
    clock.advance(200)
    assert limiter.retry_after("a") is None
    # And the budget after it is a full one.
    limiter.record_failure("a")
    limiter.record_failure("a")
    assert limiter.retry_after("a") is None


def test_a_failure_racing_a_lock_still_counts_against_its_other_sources() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=1, lockout_seconds=300)
    limiter.record_failures({"a"})
    clock.advance(100)
    limiter.record_failures({"a", "b"})
    assert limiter.retry_after("a") == 200
    assert limiter.retry_after("b") == 300


def test_one_sweep_serves_a_failure_against_several_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    limiter = make_limiter(clock, max_failures=3, window_seconds=60, sweep_threshold=10)
    for index in range(11):
        limiter.record_failure(f"recent-{index}")
    sweeps: list[float] = []
    sweep = limiter._sweep

    def counting_sweep(now: float, cutoff: float) -> None:
        sweeps.append(now)
        sweep(now, cutoff)

    monkeypatch.setattr(limiter, "_sweep", counting_sweep)
    limiter.record_failures({"a", "b"})
    # Every tracked source failed inside the window, so the sweep drops
    # nothing and the limiter is still over its threshold when the second
    # source is counted: the table is walked once all the same.
    assert sweeps == [clock.now]
    assert len(limiter._sources) == 13


def test_a_sweep_keeps_the_locked_bucket_a_failure_shares() -> None:
    clock = FakeClock()
    limiter = make_limiter(
        clock, max_failures=3, window_seconds=60, lockout_seconds=300, sweep_threshold=2
    )
    # Each failure names a fresh address and the bucket they all share; from
    # the third on, the limiter is over its threshold and sweeps first.
    for index in range(3):
        limiter.record_failures({"shared", f"198.51.100.{index}"})
    assert limiter.retry_after("shared") == 300
    clock.advance(61)
    limiter.record_failures({"203.0.113.9"})
    # The addresses fell out of the window and were swept. The bucket is
    # locked, so it was kept and its lockout runs its course.
    assert set(limiter._sources) == {"shared", "203.0.113.9"}
    assert limiter.retry_after("shared") == 239


# --- Which sources an attempt counts against --------------------------------

FORWARDED = b"x-forwarded-for"
PEER = ("192.0.2.10", 40000)


@pytest.mark.parametrize(
    ("headers", "client", "trusted_proxy", "expected"),
    [
        pytest.param([], PEER, False, {"192.0.2.10"}, id="no-header"),
        pytest.param([], None, False, {"unknown"}, id="no-header-no-peer"),
        pytest.param([(FORWARDED, b"   ")], PEER, False, {"192.0.2.10"}, id="blank-header"),
        pytest.param(
            [(FORWARDED, b"203.0.113.7")],
            PEER,
            False,
            {"untrusted-forwarded", "192.0.2.10"},
            id="header",
        ),
        pytest.param(
            [(FORWARDED, b"198.51.100.1"), (FORWARDED, b"198.51.100.2, 203.0.113.9")],
            PEER,
            False,
            {"untrusted-forwarded", "192.0.2.10"},
            id="repeated-headers",
        ),
        pytest.param(
            [(FORWARDED, b"203.0.113.7")],
            None,
            False,
            {"untrusted-forwarded", "unknown"},
            id="header-no-peer",
        ),
        # uvicorn accepts any text as the forwarded address, so a peer it
        # trusts can report the bucket's own name; that is one source.
        pytest.param(
            [(FORWARDED, b"untrusted-forwarded")],
            ("untrusted-forwarded", 0),
            False,
            {"untrusted-forwarded"},
            id="header-naming-the-bucket",
        ),
        pytest.param([], PEER, True, {"192.0.2.10"}, id="trusted-no-header"),
        pytest.param([(FORWARDED, b"   ")], PEER, True, {"192.0.2.10"}, id="trusted-blank-header"),
        pytest.param(
            [(FORWARDED, b"198.51.100.1, 203.0.113.7")],
            PEER,
            True,
            {"203.0.113.7"},
            id="trusted-rightmost-entry",
        ),
        pytest.param(
            [(FORWARDED, b"198.51.100.1"), (FORWARDED, b"198.51.100.2, 203.0.113.9")],
            PEER,
            True,
            {"203.0.113.9"},
            id="trusted-repeated-headers",
        ),
    ],
)
def test_client_sources(
    headers: list[tuple[bytes, bytes]],
    client: tuple[str, int] | None,
    trusted_proxy: bool,
    expected: set[str],
) -> None:
    from cfm.ratelimit import client_sources  # noqa: PLC0415

    request = make_request(headers, client=client)
    assert client_sources(request, trusted_proxy=trusted_proxy) == expected


def test_untrusted_forwarded_requests_share_one_bucket() -> None:
    # What uvicorn's proxy-header handling leaves behind: the reported
    # client address is whatever the header claimed. Each claim is charged,
    # and so is the one bucket every request carrying the header shares, so
    # a new claim does not buy a new budget.
    from cfm.ratelimit import client_sources  # noqa: PLC0415

    first = make_request([(FORWARDED, b"203.0.113.7")], client=("203.0.113.7", 40000))
    second = make_request([(FORWARDED, b"198.51.100.9")], client=("198.51.100.9", 40000))
    shared = client_sources(first, trusted_proxy=False) & client_sources(
        second, trusted_proxy=False
    )
    # The name operators read in the lockout log line and in the guide.
    assert shared == {"untrusted-forwarded"}


def test_sending_the_header_still_counts_against_the_address() -> None:
    # One peer, with and without a header nobody vouches for. Every source
    # the plain attempt counts against, the other counts against too, so
    # leaving the header out reaches no budget that sending it spared.
    from cfm.ratelimit import client_sources  # noqa: PLC0415

    plain = client_sources(make_request([]), trusted_proxy=False)
    forwarded = client_sources(make_request([(FORWARDED, b"203.0.113.7")]), trusted_proxy=False)
    assert plain < forwarded
