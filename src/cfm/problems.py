"""RFC 9457 problem responses and application-wide exception handlers."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import DomainError, RateLimitedError

PROBLEM_TYPE = "https://github.com/NishikawaButterfly/critical-facilities-manager#problem-details"
PROBLEM_MEDIA_TYPE = "application/problem+json"
UNAUTHORIZED_STATUS = 401


def problem_response(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    error_code: str,
    invalid_params: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": PROBLEM_TYPE,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "error_code": error_code,
    }
    if invalid_params:
        body["invalid_params"] = invalid_params
    response_headers = dict(headers) if headers else {}
    if status == UNAUTHORIZED_STATUS:
        response_headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        body,
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=response_headers or None,
    )


async def _handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, DomainError):  # pragma: no cover - registration guarantees the type
        raise exc
    headers: dict[str, str] | None = None
    if isinstance(exc, RateLimitedError):
        headers = {"Retry-After": str(exc.retry_after_seconds)}
    return problem_response(
        request,
        status=exc.status_code,
        title=exc.title,
        detail=exc.detail,
        error_code=exc.error_code,
        headers=headers,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # pragma: no cover
        raise exc
    invalid_params = [
        {
            "name": ".".join(str(part) for part in error["loc"]),
            "reason": str(error["msg"]),
        }
        for error in exc.errors()
    ]
    return problem_response(
        request,
        status=422,
        title="Request validation failed",
        detail="One or more request fields are invalid.",
        error_code="api.validation_failed",
        invalid_params=invalid_params,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover
        raise exc
    return problem_response(
        request,
        status=exc.status_code,
        title="HTTP error",
        detail=str(exc.detail),
        error_code="api.http_error",
    )


def register_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
