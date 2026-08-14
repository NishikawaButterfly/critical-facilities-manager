"""Per-source throttling and lockout for failed token authentication.

The limiter counts failed bearer authentications per network source in a
sliding window and locks a source out once it crosses the limit. State
lives in process memory behind a lock: exact for the single-process
deployment story (SQLite, one uvicorn process) and honest about the
multi-process one — with several workers each process keeps its own
counters, so the effective limit is multiplied by the worker count. That
trade-off is deliberate: a database-backed limiter would put a write on
every failed attempt and a read on every request into the authentication
hot path, and per-process limits still cap an online attacker's rate.

What this defends against: online token guessing and noisy scanning from
a single source. What it does not defend against: offline attacks (no
secret material leaves the server anyway) and attacks distributed over
many sources, which per-source accounting cannot see.

Which address counts as the source is decided in :func:`client_source`,
and it is the part an attacker gets a say in: see its docstring for what
happens to requests that carry a forwarded header nobody vouched for.

Memory stays bounded: once more than ``sweep_threshold`` sources are
tracked, recording a failure first drops every source that is neither
locked nor recently failing.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Request

logger = logging.getLogger(__name__)

UNKNOWN_SOURCE = "unknown"

# The single bucket every request carrying an unvouched-for forwarded
# header counts into. Not a valid host: no peer can collide with it.
UNTRUSTED_FORWARDED_SOURCE = "untrusted-forwarded"

DEFAULT_SWEEP_THRESHOLD = 1024


def client_source(request: Request, *, trusted_proxy: bool) -> str:
    """The network source an authentication attempt is attributed to.

    With ``trusted_proxy``, the operator states that a reverse proxy they
    control sits directly in front of the application and appends the real
    client address as the final ``X-Forwarded-For`` entry (what nginx,
    traefik, and caddy do). Only that final entry is used; earlier entries
    arrived from the outside and remain attacker-controlled.

    Without it, the header is worth nothing — and the application cannot
    fall back to the connection's peer address, because it may no longer
    have one: uvicorn enables proxy-header handling by default (trusting
    ``127.0.0.1``) and replaces the client address with an entry from the
    header before any application code runs. The address such a request
    reports is therefore attacker-controlled as far as this function can
    tell, and it must not partition the counters, or rotating the header
    would buy a fresh failure budget per invented value. Every request
    carrying the header shares :data:`UNTRUSTED_FORWARDED_SOURCE` instead,
    so those attempts accumulate against one counter and lock out
    together. The cost is that clients of a real proxy the operator never
    declared share that one bucket and can lock each other out; the fix
    for that is to declare the proxy (see :class:`cfm.config.Settings`) or
    to stop uvicorn from rewriting the address.

    Requests with no forwarded header keep being attributed to the address
    the application is given, which is the peer address whenever nothing
    upstream substituted one.
    """
    forwarded = ",".join(request.headers.getlist("x-forwarded-for")).strip()
    if forwarded:
        if trusted_proxy:
            return forwarded.rsplit(",", 1)[-1].strip()
        return UNTRUSTED_FORWARDED_SOURCE
    if request.client is not None:
        return request.client.host
    return UNKNOWN_SOURCE


@dataclass
class _SourceState:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class AuthRateLimiter:
    """Sliding-window failure counter with lockout, per source.

    ``max_failures`` failed authentications within ``window_seconds`` lock
    the source for ``lockout_seconds``. A ``max_failures`` of zero disables
    the limiter. The clock is injectable and monotonic; wall-clock jumps
    never shorten or extend a lockout.
    """

    def __init__(
        self,
        *,
        max_failures: int,
        window_seconds: float,
        lockout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sweep_threshold: int = DEFAULT_SWEEP_THRESHOLD,
    ) -> None:
        self._max_failures = max_failures
        self._window = float(window_seconds)
        self._lockout = float(lockout_seconds)
        self._clock = clock
        self._sweep_threshold = sweep_threshold
        self._mutex = threading.Lock()
        self._sources: dict[str, _SourceState] = {}

    @property
    def enabled(self) -> bool:
        return self._max_failures > 0

    def retry_after(self, source: str) -> int | None:
        """Whole seconds a locked source must wait, or None when not locked."""
        if not self.enabled:
            return None
        now = self._clock()
        with self._mutex:
            state = self._sources.get(source)
            if state is None or state.locked_until <= now:
                return None
            return max(1, math.ceil(state.locked_until - now))

    def record_failure(self, source: str) -> None:
        """Count one failed authentication; lock the source at the limit."""
        if not self.enabled:
            return
        now = self._clock()
        cutoff = now - self._window
        with self._mutex:
            if len(self._sources) > self._sweep_threshold:
                self._sweep(now, cutoff)
            state = self._sources.setdefault(source, _SourceState())
            state.failures = [moment for moment in state.failures if moment > cutoff]
            state.failures.append(now)
            if len(state.failures) >= self._max_failures:
                state.failures.clear()
                state.locked_until = now + self._lockout
                logger.warning(
                    "Locked authentication source %s for %.0f seconds after "
                    "%d failures within %.0f seconds.",
                    source,
                    self._lockout,
                    self._max_failures,
                    self._window,
                )

    def _sweep(self, now: float, cutoff: float) -> None:
        """Drop sources that are neither locked nor recently failing."""
        stale = [
            source
            for source, state in self._sources.items()
            if state.locked_until <= now and all(moment <= cutoff for moment in state.failures)
        ]
        for source in stale:
            del self._sources[source]
