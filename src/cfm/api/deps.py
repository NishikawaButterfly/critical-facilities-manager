"""Shared API dependencies: session, authentication, authorization, pagination.

Authentication is a bearer API token (``Authorization: Bearer <token>``)
resolved to an active user; the authenticated username is the actor recorded
in every audit entry. Failed authentications are throttled per network
source (see :mod:`cfm.ratelimit`): a locked source receives 429 before any
token is hashed or looked up, and only failed bearer lookups count toward
the limit — successful authentications and requests without credentials
never do. Role authorization happens at include time: routers sit under a
dependency that either admits any authenticated reader and restricts writes
to engineers and admins (domain routes) or requires the admin role outright
(user and token management). Site authorization happens inside the
endpoints: once the write's target is loaded, a :class:`cfm.authz.SiteAccess`
check requires a grant covering the target's site (see :mod:`cfm.authz`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..authz import SiteAccess
from ..config import Settings
from ..database import get_session
from ..domain import UserRole
from ..errors import AuthenticationError, PermissionDeniedError, RateLimitedError
from ..evidence import EvidenceStore
from ..models import User
from ..ratelimit import AuthRateLimiter, client_source
from ..services.tokens import resolve_token

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WRITE_ROLES = frozenset({UserRole.ENGINEER, UserRole.ADMIN})

# One constant body for every throttled response, whatever the token was:
# the 429 must not reveal more than "this source is locked".
RATE_LIMITED_DETAIL = (
    "Too many failed authentication attempts from this source; "
    "retry after the number of seconds given in the Retry-After header."
)

SessionDep = Annotated[Session, Depends(get_session)]

_bearer_scheme = HTTPBearer(
    auto_error=False,
    description="API token minted through /users/{user_id}/tokens (or the bootstrap script).",
)


def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    settings: Settings = request.app.state.settings
    limiter: AuthRateLimiter = request.app.state.auth_limiter
    source = client_source(request, trusted_proxy=settings.trusted_proxy)
    retry_after = limiter.retry_after(source)
    if retry_after is not None:
        # Cheap rejection first: a locked source is turned away before any
        # hashing or token lookup, with or without credentials attached.
        raise RateLimitedError(RATE_LIMITED_DETAIL, retry_after_seconds=retry_after)
    if credentials is None:
        raise AuthenticationError(
            "Authentication is required: send an API token as 'Authorization: Bearer <token>'.",
            error_code="auth.credentials_required",
        )
    try:
        return resolve_token(session, credentials.credentials)
    except AuthenticationError:
        limiter.record_failure(source)
        raise


CurrentUserDep = Annotated[User, Depends(get_current_user)]


def get_actor(user: CurrentUserDep) -> str:
    """The authenticated username, recorded as the actor in every audit entry."""
    return user.username


ActorDep = Annotated[str, Depends(get_actor)]


def require_domain_access(request: Request, user: CurrentUserDep) -> User:
    """Domain routes: any authenticated user reads, engineers and admins write."""
    if request.method in READ_METHODS or UserRole(user.role) in WRITE_ROLES:
        return user
    raise PermissionDeniedError(
        f"Role {user.role!r} may only read; {request.method} requires an engineer or admin."
    )


def require_admin(user: CurrentUserDep) -> User:
    """User and token management: admins only."""
    if UserRole(user.role) is UserRole.ADMIN:
        return user
    raise PermissionDeniedError("User and token management requires the admin role.")


def get_site_access(session: SessionDep, user: CurrentUserDep) -> SiteAccess:
    """Site-grant checks for this request; endpoints guard writes with it."""
    return SiteAccess(session, user)


SiteAccessDep = Annotated[SiteAccess, Depends(get_site_access)]


def get_evidence_store(request: Request) -> EvidenceStore:
    """The application's content-addressed evidence store."""
    store: EvidenceStore = request.app.state.evidence_store
    return store


EvidenceStoreDep = Annotated[EvidenceStore, Depends(get_evidence_store)]


def get_app_settings(request: Request) -> Settings:
    """The settings the application was created with."""
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


def get_page_params(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


PageDep = Annotated[PageParams, Depends(get_page_params)]
