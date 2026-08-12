"""Domain errors surfaced to clients as RFC 9457 problem responses."""

from __future__ import annotations

from fastapi import HTTPException


class DomainError(Exception):
    """Base class for domain rule violations."""

    def __init__(self, detail: str, *, status_code: int, error_code: str, title: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.error_code = error_code
        self.title = title


class NotFoundError(DomainError):
    """The resource addressed by the URL does not exist."""

    def __init__(self, detail: str, *, error_code: str = "resource.not_found") -> None:
        super().__init__(
            detail,
            status_code=404,
            error_code=error_code,
            title="Resource not found",
        )


class ConflictError(DomainError):
    """The request is well-formed but conflicts with the current state."""

    def __init__(self, detail: str, *, error_code: str) -> None:
        super().__init__(
            detail,
            status_code=409,
            error_code=error_code,
            title="Request conflicts with current state",
        )


class RuleViolationError(DomainError):
    """The request payload breaks a domain rule."""

    def __init__(self, detail: str, *, error_code: str) -> None:
        super().__init__(
            detail,
            status_code=422,
            error_code=error_code,
            title="Domain rule violated",
        )


class AuthenticationError(DomainError):
    """The request lacks valid authentication credentials."""

    def __init__(self, detail: str, *, error_code: str = "auth.invalid_token") -> None:
        super().__init__(
            detail,
            status_code=401,
            error_code=error_code,
            title="Authentication required",
        )


class RateLimitedError(DomainError):
    """The source sent too many failed authentication attempts."""

    def __init__(
        self,
        detail: str,
        *,
        retry_after_seconds: int,
        error_code: str = "auth.rate_limited",
    ) -> None:
        super().__init__(
            detail,
            status_code=429,
            error_code=error_code,
            title="Too many requests",
        )
        self.retry_after_seconds = retry_after_seconds


class PermissionDeniedError(DomainError):
    """The authenticated user is not allowed to perform the request."""

    def __init__(self, detail: str, *, error_code: str = "auth.forbidden") -> None:
        super().__init__(
            detail,
            status_code=403,
            error_code=error_code,
            title="Permission denied",
        )


class PayloadTooLargeError(DomainError):
    """An uploaded evidence file exceeds the configured size limit."""

    def __init__(self, detail: str, *, error_code: str = "evidence.file_too_large") -> None:
        super().__init__(
            detail,
            status_code=413,
            error_code=error_code,
            title="Uploaded file too large",
        )


class EvidenceCorruptedError(DomainError):
    """A stored evidence object failed hash verification and was refused.

    Deliberately a 500: the client's request was fine — the server can no
    longer prove the stored bytes are the ones that were attached, and
    refusing loudly beats serving silently wrong evidence.
    """

    def __init__(self, detail: str, *, error_code: str = "evidence.corrupted") -> None:
        super().__init__(
            detail,
            status_code=500,
            error_code=error_code,
            title="Stored evidence failed verification",
        )


class RequestBodyTooLargeError(OSError, HTTPException):
    """An upload request body exceeds the transport limit; rendered as 413.

    Raised by :class:`cfm.bodylimit.EvidenceBodyLimitMiddleware` while the
    multipart body is still streaming, so it cannot be a
    :class:`DomainError`: it must inherit ``OSError`` for the multipart
    parser to clean up its spooled temporary files, and ``HTTPException``
    for FastAPI's body-parsing guard to re-raise it unchanged instead of
    converting it into a generic 400. :mod:`cfm.problems` registers a
    dedicated handler that renders it like every other problem response.
    """

    error_code = "evidence.request_too_large"
    title = "Request body too large"

    def __init__(self, max_body_bytes: int) -> None:
        detail = (
            f"The request body exceeds the evidence upload limit of {max_body_bytes} "
            "bytes (the configured file cap plus multipart framing)."
        )
        OSError.__init__(self, detail)
        HTTPException.__init__(self, status_code=413, detail=detail)
