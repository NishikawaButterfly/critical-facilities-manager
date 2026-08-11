"""Create the very first admin user and mint its API token.

This is the only path that creates a user without an authenticated caller,
and it refuses to run as soon as any user exists — after that, user and
token management happens through the API with an admin token. The audit
entries record the new admin as its own creator, which is exactly what
happened.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from .config import get_settings
from .database import build_engine, build_session_factory
from .domain import UserRole
from .migrate import ensure_fresh_schema
from .models import User
from .services.tokens import create_token
from .services.users import create_user


class BootstrapError(RuntimeError):
    """Raised when the target database already contains users."""


@dataclass(frozen=True)
class BootstrapResult:
    """The created admin and its token secret, shown exactly once."""

    user_id: str
    username: str
    token_label: str
    token: str


def bootstrap_admin(
    username: str,
    display_name: str,
    *,
    label: str = "bootstrap",
    database_url: str | None = None,
) -> BootstrapResult:
    """Create the first admin plus one API token and return the secret once."""
    url = database_url or get_settings().database_url
    ensure_fresh_schema(url)
    engine = build_engine(url)
    try:
        session_factory = build_session_factory(engine)
        with session_factory() as session:
            existing = session.scalar(select(func.count()).select_from(User)) or 0
            if existing:
                raise BootstrapError(
                    "The database already contains users; the bootstrap only creates "
                    "the first admin. Manage further users through the API with an "
                    "admin token."
                )
            user = create_user(
                session,
                username,
                username=username,
                display_name=display_name,
                role=UserRole.ADMIN,
            )
            token, secret = create_token(
                session,
                username,
                user,
                label=label,
                expires_at=None,
            )
            return BootstrapResult(
                user_id=user.id,
                username=user.username,
                token_label=token.label,
                token=secret,
            )
    finally:
        engine.dispose()
