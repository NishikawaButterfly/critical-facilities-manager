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

DEFAULT_SWEEP_THRESHOLD = 1024


def client_source(request: Request, *, trusted_proxy: bool) -> str:
    """The network source an authentication attempt is attributed to.

    By default this is the peer address of the transport connection. The
    ``X-Forwarded-For`` header is client-writable and therefore ignored
    unless ``trusted_proxy`` states that a reverse proxy the operator
    controls sits directly in front of the application and appends the real
    client address as the final entry (what nginx, traefik, and caddy do).
    Only that final entry is trusted; earlier entries arrived from the
    outside and remain attacker-controlled.
    """
    if trusted_proxy:
        forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
        if forwarded.strip():
            return forwarded.rsplit(",", 1)[-1].strip()
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
