"""User account services.

Users have no passwords and cannot register themselves: authentication
happens exclusively through API tokens (see :mod:`cfm.services.tokens`)
minted by an admin. Interactive login is future work that arrives together
with a frontend. Users are deactivated, never deleted, so the usernames
recorded in the audit trail keep resolving.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..domain import UserRole
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import User
from .site_grants import create_initial_grants

ENTITY_TYPE = "user"

MAX_USERNAME_LENGTH = 64
USERNAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def user_snapshot(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
    }


def validate_username(username: str) -> str:
    """Require a lowercase slug so usernames read cleanly in the audit trail."""
    if len(username) > MAX_USERNAME_LENGTH or not USERNAME_PATTERN.fullmatch(username):
        raise RuleViolationError(
            "A username must be a lowercase slug (letters, digits, and inner hyphens) "
            f"of at most {MAX_USERNAME_LENGTH} characters.",
            error_code="user.username_invalid",
        )
    return username


def get_user(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError(
            f"User {user_id} was not found.",
            error_code="user.not_found",
        )
    return user


def find_active_user(session: Session, username: str) -> User | None:
    """Return the active user with this username, or None."""
    return session.scalar(select(User).where(User.username == username, User.is_active))


def create_user(
    session: Session,
    actor: str,
    *,
    username: str,
    display_name: str,
    role: UserRole,
    site_ids: list[str] | None = None,
) -> User:
    """Create a user together with their initial site grants.

    ``site_ids`` limits the user's write authority to those sites; the
    default is a single installation-wide grant, which is what every user
    had before site scoping existed. The grants are part of this one
    creation action, so they appear in the user's "created" audit entry
    (as scope labels) instead of as entries of their own.
    """
    validate_username(username)
    existing = session.scalar(select(User).where(User.username == username))
    if existing is not None:
        raise ConflictError(
            f"Username {username!r} is already taken.",
            error_code="user.username_taken",
        )
    user = User(
        username=username,
        display_name=display_name,
        role=role.value,
        is_active=True,
    )
    session.add(user)
    session.flush()
    site_scopes = create_initial_grants(session, user, site_ids)
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=user.id,
        action="created",
        before=None,
        after={**user_snapshot(user), "site_scopes": site_scopes},
    )
    session.commit()
    return user


def deactivate_user(session: Session, actor: str, user: User) -> User:
    if not user.is_active:
        raise ConflictError(
            f"User {user.username!r} is already inactive.",
            error_code="user.already_inactive",
        )
    if UserRole(user.role) is UserRole.ADMIN:
        active_admins = (
            session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.role == UserRole.ADMIN.value, User.is_active)
            )
            or 0
        )
        if active_admins <= 1:
            raise ConflictError(
                f"User {user.username!r} is the last active admin and cannot be deactivated.",
                error_code="user.last_active_admin",
            )
    user.is_active = False
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=user.id,
        action="deactivated",
        before={"is_active": True},
        after={"is_active": False},
    )
    session.commit()
    return user
