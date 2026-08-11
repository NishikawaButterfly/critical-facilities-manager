"""Domain errors surfaced to clients as RFC 9457 problem responses."""

from __future__ import annotations


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
