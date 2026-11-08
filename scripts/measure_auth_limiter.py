"""Measure what the authentication limiter costs with one source and with two.

Usage:
    python scripts/measure_auth_limiter.py [--number N] [--repeat R]
        [--requests N]

A failed authentication counts against one source, or against two when the
request carries an X-Forwarded-For header that no trusted proxy vouches for
(see ``cfm.ratelimit.client_sources``). Two limiter operations sit on the
authentication path, and both are timed with one source and with two:

- the lockout check, which runs before every authentication, for sources
  that have failed before and are not locked;
- the failure path, which runs after every failed authentication, in a
  steady state: failures arrive seven seconds apart on the limiter's clock,
  so each source holds nine of them inside the default 60-second window,
  one short of the default limit of ten.

Each figure is the best of R timeit runs of N calls, in microseconds per
call. The four timings take turns, one run each per repeat, so a change in
the machine's speed while the script runs lands on all of them alike; many
short runs rather than a few long ones make it likelier that the best of
them saw the machine undisturbed.

Then whole failed authentications are timed: GET /api/v1/me with a
token that does not exist, sent in process to the ASGI application on a
fresh SQLite database, alternating requests without the header and with it,
and the median of N of each is reported. The limiter keeps its default
limits there and is replaced, outside the timing, before any source can
reach the limit, so every request is checked, looked up and counted.

The figures depend on the machine that runs the script.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import platform
import sqlite3
import statistics
import tempfile
import time
import timeit
from collections.abc import Callable
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from starlette.types import Message, Scope

from cfm.config import Settings
from cfm.main import create_app
from cfm.ratelimit import UNTRUSTED_FORWARDED_SOURCE, AuthRateLimiter

# The documented defaults of CFM_AUTH_MAX_FAILURES,
# CFM_AUTH_FAILURE_WINDOW_SECONDS and CFM_AUTH_LOCKOUT_SECONDS.
MAX_FAILURES = 10
WINDOW_SECONDS = 60.0
LOCKOUT_SECONDS = 300.0
# Seconds between failures in the steady state: nine fit inside the window.
FAILURE_SPACING = 7.0

PEER_ADDRESS = "192.0.2.10"
ONE_SOURCE = frozenset({PEER_ADDRESS})
TWO_SOURCES = frozenset({UNTRUSTED_FORWARDED_SOURCE, PEER_ADDRESS})

API_PREFIX = "/api/v1"
UNAUTHORIZED = 401
# Requests of each kind sent and discarded before the timed ones.
WARMUP_REQUESTS = 50
# Each round sends one request of each kind, which is two failures against
# the peer address; four rounds stay under the limit of ten.
ROUNDS_PER_LIMITER = 4


class SteppingClock:
    """A monotonic clock that moves on by a fixed step every time it is read."""

    def __init__(self, step: float) -> None:
        self._step = step
        self._now = 0.0

    def __call__(self) -> float:
        self._now += self._step
        return self._now


def make_limiter(clock: Callable[[], float] = time.monotonic) -> AuthRateLimiter:
    return AuthRateLimiter(
        max_failures=MAX_FAILURES,
        window_seconds=WINDOW_SECONDS,
        lockout_seconds=LOCKOUT_SECONDS,
        clock=clock,
    )


def time_limiter(*, number: int, repeat: int) -> dict[tuple[str, int], float]:
    """Microseconds per call of each operation, keyed by name and source count.

    Every repeat runs each of the four timings once, in turn, so a change in
    the machine's speed while the script runs lands on all of them alike,
    and the best of the repeats is kept for each.
    """
    statements: dict[tuple[str, int], Callable[[], object]] = {}
    limiters: list[tuple[AuthRateLimiter, frozenset[str]]] = []
    for sources in (ONE_SOURCE, TWO_SOURCES):
        checked = make_limiter()
        for _ in range(MAX_FAILURES - 1):
            checked.record_failures(sources)
        failing = make_limiter(SteppingClock(FAILURE_SPACING))
        # Into the steady state first, where every source holds nine failures.
        for _ in range(2 * MAX_FAILURES):
            failing.record_failures(sources)
        statements["lockout check", len(sources)] = partial(checked.retry_after_any, sources)
        statements["failure path", len(sources)] = partial(failing.record_failures, sources)
        limiters += [(checked, sources), (failing, sources)]
    best = dict.fromkeys(statements, math.inf)
    for _ in range(repeat):
        for key, statement in statements.items():
            best[key] = min(best[key], timeit.timeit(statement, number=number) / number * 1e6)
    if any(limiter.retry_after_any(sources) is not None for limiter, sources in limiters):
        raise RuntimeError("a source locked while the limiter was being timed")
    return best


async def failed_authentication(
    application: FastAPI, headers: list[tuple[bytes, bytes]]
) -> tuple[int, float]:
    """Send one request straight to the ASGI application.

    Returns the response status and the time the application took, in
    microseconds, from the call until it returned.
    """
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"{API_PREFIX}/me",
        "raw_path": f"{API_PREFIX}/me".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": (PEER_ADDRESS, 40000),
        "server": ("testserver", 80),
    }
    request_sent = False
    response_complete = asyncio.Event()
    status = 0

    async def receive() -> Message:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await response_complete.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body" and not message.get("more_body", False):
            response_complete.set()

    started = time.perf_counter()
    await application(scope, receive, send)
    return status, (time.perf_counter() - started) * 1e6


async def time_failed_authentications(requests: int) -> tuple[float, float]:
    """Median microseconds of a failed authentication without and with the header."""
    plain = [(b"host", b"testserver"), (b"authorization", b"Bearer not-a-token")]
    forwarded = [*plain, (b"x-forwarded-for", b"203.0.113.7")]
    timings: dict[str, list[float]] = {"plain": [], "forwarded": []}
    with tempfile.TemporaryDirectory(prefix="cfm-measure-", ignore_cleanup_errors=True) as name:
        directory = Path(name)
        settings = Settings(
            database_url=f"sqlite:///{(directory / 'measure.db').as_posix()}",
            db_auto_upgrade=True,
            evidence_dir=directory / "evidence",
            api_prefix=API_PREFIX,
            docs_enabled=False,
            web_enabled=False,
            trusted_proxy=False,
        )
        application = create_app(settings)
        async with application.router.lifespan_context(application):
            for round_index in range(WARMUP_REQUESTS + requests):
                if round_index % ROUNDS_PER_LIMITER == 0:
                    application.state.auth_limiter = make_limiter()
                for kind, headers in (("plain", plain), ("forwarded", forwarded)):
                    status, elapsed = await failed_authentication(application, headers)
                    if status != UNAUTHORIZED:
                        raise RuntimeError(f"expected 401 for a missing token, got {status}")
                    if round_index >= WARMUP_REQUESTS:
                        timings[kind].append(elapsed)
    return statistics.median(timings["plain"]), statistics.median(timings["forwarded"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the authentication limiter.")
    parser.add_argument(
        "--number", type=int, default=500, help="Calls per timeit run (default: 500)."
    )
    parser.add_argument(
        "--repeat", type=int, default=2_000, help="timeit runs of each timing (default: 2000)."
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=2_000,
        help="Timed requests of each kind through the application (default: 2000).",
    )
    arguments = parser.parse_args()
    number, repeat = arguments.number, arguments.repeat

    print(f"Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}")
    print(f"Limiter, best of {repeat} runs of {number} calls, microseconds per call:")
    print(f"  {'':16}{'one source':>12}{'two sources':>13}{'difference':>12}")
    timings = time_limiter(number=number, repeat=repeat)
    for label in ("lockout check", "failure path"):
        one, two = timings[label, 1], timings[label, 2]
        print(f"  {label:16}{one:12.2f}{two:13.2f}{two - one:+12.2f}")

    plain, forwarded = asyncio.run(time_failed_authentications(arguments.requests))
    print(
        f"Failed authentication through the ASGI application, median of "
        f"{arguments.requests} requests each, microseconds:"
    )
    print(f"  without the header (one source)  {plain:9.1f}")
    print(f"  with the header (two sources)    {forwarded:9.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
