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

An attempt can belong to more than one source. Which ones is decided in
:func:`client_sources`, and it is the part an attacker gets a say in: see
its docstring for what happens to requests that carry a forwarded header
nobody vouched for. The attempt is refused while any of its sources is
locked, and its failure counts against every one of them, so attempts
that share one source never get past that source's budget, whatever else
they present.

Memory stays bounded: once more than ``sweep_threshold`` sources are
tracked, recording a failure first drops every source that is neither
locked nor recently failing.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from fastapi import Request

logger = logging.getLogger(__name__)

UNKNOWN_SOURCE = "unknown"

# The single bucket every request carrying an unvouched-for forwarded
# header counts into. Not a valid host: no peer can collide with it.
UNTRUSTED_FORWARDED_SOURCE = "untrusted-forwarded"

DEFAULT_SWEEP_THRESHOLD = 1024


def client_sources(request: Request, *, trusted_proxy: bool) -> frozenset[str]:
    """Every network source an authentication attempt is counted against.

    With ``trusted_proxy``, the operator states that a reverse proxy they
    control sits directly in front of the application and appends the real
    client address as the final ``X-Forwarded-For`` entry (what nginx,
    traefik, and caddy do). That final entry is the one source; earlier
    entries arrived from the outside and remain attacker-controlled.

    Without it, the header is worth nothing, and as far as this function
    can tell neither is the address the request reports: uvicorn enables
    proxy-header handling by default (trusting ``127.0.0.1``) and replaces
    the client address with an entry from the header before any
    application code runs. Such a request therefore counts against two
    sources. One is :data:`UNTRUSTED_FORWARDED_SOURCE`, shared by every
    request carrying the header, so that rotating the header buys no fresh
    failure budget: those attempts accumulate against one counter and lock
    out together. The other is the address the request reports, which is
    the peer's own whenever nothing upstream substituted one, so that the
    same peer gains no second budget by leaving the header out. The cost
    of the shared bucket is that clients of a real proxy the operator
    never declared share it and can lock each other out; the fix for that
    is to declare the proxy (see :class:`cfm.config.Settings`) or to stop
    uvicorn from rewriting the address.

    What this cannot see past is an address uvicorn did substitute. A peer
    uvicorn trusts is charged under whatever address its header named,
    which may be somebody else's, and its own address keeps a budget for
    its attempts without the header.

    Requests with no forwarded header count against the address the
    application is given alone, which is the peer address whenever nothing
    upstream substituted one.
    """
    reported = request.client.host if request.client is not None else UNKNOWN_SOURCE
    forwarded = ",".join(request.headers.getlist("x-forwarded-for")).strip()
    if not forwarded:
        return frozenset((reported,))
    if trusted_proxy:
        return frozenset((forwarded.rsplit(",", 1)[-1].strip(),))
    return frozenset((UNTRUSTED_FORWARDED_SOURCE, reported))


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

    The operations take every source an attempt is counted against (see
    :func:`client_sources`), and each source keeps its own count and its
    own lockout. An attempt is refused while any of its sources is locked,
    a failure counts once against each of them, and a lockout, once set,
    is never extended.
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

    def retry_after_any(self, sources: Iterable[str]) -> int | None:
        """Whole seconds until none of ``sources`` is locked, or None if none is.

        That is the longest of their remaining lockouts, rounded up, so
        that once a client has waited as long as it is told, none of the
        lockouts it was refused for is still running.
        """
        if not self.enabled:
            return None
        now = self._clock()
        latest = now
        with self._mutex:
            for source in sources:
                state = self._sources.get(source)
                if state is not None and state.locked_until > latest:
                    latest = state.locked_until
        if latest <= now:
            return None
        return max(1, math.ceil(latest - now))

    def retry_after(self, source: str) -> int | None:
        """Whole seconds a locked source must wait, or None when not locked."""
        return self.retry_after_any((source,))

    def record_failures(self, sources: Iterable[str]) -> None:
        """Count one failed authentication against each of ``sources``.

        Each source that reaches the limit is locked, and logged, on its
        own. A source that is already locked is left exactly as it is, so
        its lockout ends when it was set to end and the budget after it is
        a full one. Callers check :meth:`retry_after_any` before they
        authenticate, so a failure reaches a locked source only from an
        attempt that was checked before the lock was set.
        """
        if not self.enabled:
            return
        now = self._clock()
        cutoff = now - self._window
        with self._mutex:
            if len(self._sources) > self._sweep_threshold:
                self._sweep(now, cutoff)
            # Each source once, and in a fixed order, so that a failure
            # locking several sources logs them the same way every time.
            for source in sorted(frozenset(sources)):
                state = self._sources.setdefault(source, _SourceState())
                if state.locked_until > now:
                    continue
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

    def record_failure(self, source: str) -> None:
        """Count one failed authentication; lock the source at the limit."""
        self.record_failures((source,))

    def _sweep(self, now: float, cutoff: float) -> None:
        """Drop sources that are neither locked nor recently failing."""
        stale = [
            source
            for source, state in self._sources.items()
            if state.locked_until <= now and all(moment <= cutoff for moment in state.failures)
        ]
        for source in stale:
            del self._sources[source]
