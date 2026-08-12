"""Reject oversized evidence upload bodies before multipart parsing spools them.

FastAPI parses the whole multipart body — spooling file parts to temporary
storage — before endpoint code can inspect anything, so the per-file size
cap alone would let an arbitrarily large request fill the disk first. This
middleware bounds the transport on evidence upload routes: a body larger
than the file cap plus one mebibyte of multipart framing is rejected with
413, cheaply via Content-Length when the header is present and truthful,
and otherwise by counting the streamed bytes and aborting mid-parse with
:class:`cfm.errors.RequestBodyTooLargeError`. The dedicated handler in
:mod:`cfm.problems` renders that as a problem response; the middleware
only answers directly when the exception reaches it before any response
started. The endpoint still enforces the exact per-file cap while hashing.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import RequestBodyTooLargeError
from .problems import problem_response

MULTIPART_OVERHEAD_BYTES = 1024 * 1024
UPLOAD_PATH_SUFFIX = "/evidence-files"


class EvidenceBodyLimitMiddleware:
    """Bound the request body size on evidence upload routes."""

    def __init__(self, app: ASGIApp, *, api_prefix: str, max_file_bytes: int) -> None:
        self.app = app
        self.api_prefix = "/" + api_prefix.strip("/")
        self.max_body_bytes = max_file_bytes + MULTIPART_OVERHEAD_BYTES

    def _applies(self, scope: Scope) -> bool:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return False
        path = str(scope.get("path", "")).rstrip("/")
        return path.startswith(f"{self.api_prefix}/") and path.endswith(UPLOAD_PATH_SUFFIX)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        error = RequestBodyTooLargeError(self.max_body_bytes)
        response = problem_response(
            Request(scope),
            status=error.status_code,
            title=error.title,
            detail=str(error.detail),
            error_code=error.error_code,
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies(scope):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                # A malformed value cannot bypass the streaming byte counter.
                pass

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise RequestBodyTooLargeError(self.max_body_bytes)
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLargeError:
            if response_started:
                raise
            await self._reject(scope, receive, send)
