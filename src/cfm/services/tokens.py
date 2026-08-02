"""API token services.

Token secrets are generated server-side, returned exactly once at creation,
and stored only as SHA-256 hashes. Lookup hashes the presented secret and
confirms the match in constant time (``hmac.compare_digest``) against the
stored hash. Authentication failures share one generic message so responses
do not reveal whether a token exists, is revoked, or has expired.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import jsonable, record_audit
from ..errors import AuthenticationError, ConflictError, NotFoundError, RuleViolationError
from ..models import ApiToken, User

ENTITY_TYPE = "api_token"

TOKEN_BYTES = 32

AUTH_FAILED_DETAIL = "The API token is unknown, revoked, expired, or belongs to an inactive user."


def hash_token(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _ensure_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; make every timestamp explicitly UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def token_snapshot(token: ApiToken) -> dict[str, Any]:
    """Token metadata only; neither the secret nor its hash reaches the audit trail."""
    return {
        "id": token.id,
        "user_id": token.user_id,
        "label": token.label,
        "expires_at": jsonable(token.expires_at),
        "revoked": token.revoked,
    }


def get_token(session: Session, user: User, token_id: str) -> ApiToken:
    token = session.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise NotFoundError(
            f"API token {token_id} was not found for user {user.username!r}.",
            error_code="api_token.not_found",
        )
    return token


def create_token(
    session: Session,
    actor: str,
    user: User,
    *,
    label: str,
    expires_at: datetime | None,
) -> tuple[ApiToken, str]:
    """Create a token and return it together with the plaintext secret.

    The secret only exists in the return value; the database keeps its hash.
    """
    if not user.is_active:
        raise RuleViolationError(
            f"User {user.username!r} is inactive; inactive users cannot receive tokens.",
            error_code="api_token.user_inactive",
        )
    if expires_at is not None and _ensure_utc(expires_at) <= datetime.now(UTC):
        raise RuleViolationError(
            "The token expiry must lie in the future.",
            error_code="api_token.expiry_not_future",
        )
    secret = secrets.token_urlsafe(TOKEN_BYTES)
    token = ApiToken(
        user_id=user.id,
        label=label,
        token_hash=hash_token(secret),
        expires_at=expires_at,
        revoked=False,
    )
    session.add(token)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=token.id,
        action="created",
        before=None,
        after=token_snapshot(token),
    )
    session.commit()
    return token, secret


def revoke_token(session: Session, actor: str, token: ApiToken) -> ApiToken:
    if token.revoked:
        raise ConflictError(
            f"API token {token.id} is already revoked.",
            error_code="api_token.already_revoked",
        )
    token.revoked = True
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=token.id,
        action="revoked",
        before={"revoked": False},
        after={"revoked": True},
    )
    session.commit()
    return token


def resolve_token(session: Session, secret: str) -> User:
    """Return the active user behind a presented token secret, or raise a 401."""
    digest = hash_token(secret)
    token = session.scalar(select(ApiToken).where(ApiToken.token_hash == digest))
    if token is None or not hmac.compare_digest(digest, token.token_hash):
        raise AuthenticationError(AUTH_FAILED_DETAIL)
    if token.revoked:
        raise AuthenticationError(AUTH_FAILED_DETAIL)
    if token.expires_at is not None and _ensure_utc(token.expires_at) <= datetime.now(UTC):
        raise AuthenticationError(AUTH_FAILED_DETAIL)
    user = session.get(User, token.user_id)
    if user is None:  # pragma: no cover - the foreign key keeps the user present
        raise AuthenticationError(AUTH_FAILED_DETAIL)
    if not user.is_active:
        raise AuthenticationError(AUTH_FAILED_DETAIL)
    return user
