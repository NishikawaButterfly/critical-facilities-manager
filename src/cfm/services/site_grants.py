"""Site grant services: where a user's write authority applies.

A grant covers one site's whole subtree, or the whole installation when
``site_id`` is null. Grants are plain rows — created and deleted, both
audited — because unlike tokens they carry no secret worth retiring in
place; the audit trail keeps the full history either way.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..authz import scope_label
from ..domain import LocationKind
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import Location, SiteGrant, User

ENTITY_TYPE = "site_grant"


def grant_snapshot(grant: SiteGrant) -> dict[str, Any]:
    return {
        "id": grant.id,
        "user_id": grant.user_id,
        "site_id": grant.site_id,
        "scope": scope_label(grant.site_id),
    }


def get_grant(session: Session, user: User, grant_id: str) -> SiteGrant:
    grant = session.get(SiteGrant, grant_id)
    if grant is None or grant.user_id != user.id:
        raise NotFoundError(
            f"Site grant {grant_id} was not found for user {user.username!r}.",
            error_code="site_grant.not_found",
        )
    return grant


def list_grants(session: Session, user: User) -> list[SiteGrant]:
    return list(
        session.scalars(
            select(SiteGrant)
            .where(SiteGrant.user_id == user.id)
            .order_by(SiteGrant.created_at, SiteGrant.id)
        )
    )


def _validate_site(session: Session, site_id: str) -> Location:
    location = session.get(Location, site_id)
    if location is None:
        raise RuleViolationError(
            f"Location {site_id} was not found.",
            error_code="site_grant.site_not_found",
        )
    if location.kind != LocationKind.SITE.value:
        raise RuleViolationError(
            f"A site grant must reference a site; {location.code!r} is a {location.kind}.",
            error_code="site_grant.not_a_site",
        )
    return location


def _check_not_duplicate(session: Session, user: User, site_id: str | None) -> None:
    condition = SiteGrant.site_id.is_(None) if site_id is None else SiteGrant.site_id == site_id
    existing = session.scalar(
        select(func.count()).select_from(SiteGrant).where(SiteGrant.user_id == user.id, condition)
    )
    if existing:
        held = (
            "an installation-wide grant" if site_id is None else f"a grant covering site {site_id}"
        )
        raise ConflictError(
            f"User {user.username!r} already holds {held}.",
            error_code="site_grant.duplicate",
        )


def create_initial_grants(
    session: Session,
    user: User,
    site_ids: list[str] | None,
) -> list[str]:
    """Add a new user's initial grants and return their scope labels.

    Part of user creation: one logical action, so the grants appear in the
    user's "created" audit entry rather than as entries of their own, and
    the caller owns the commit. ``None`` means the back-compatible default,
    a single installation-wide grant.
    """
    if site_ids is None:
        session.add(SiteGrant(user_id=user.id, site_id=None))
        return [scope_label(None)]
    labels: list[str] = []
    for site_id in dict.fromkeys(site_ids):
        _validate_site(session, site_id)
        session.add(SiteGrant(user_id=user.id, site_id=site_id))
        labels.append(scope_label(site_id))
    return labels


def create_site_grant(
    session: Session,
    actor: str,
    user: User,
    *,
    site_id: str | None,
) -> SiteGrant:
    if not user.is_active:
        raise RuleViolationError(
            f"User {user.username!r} is inactive; inactive users cannot receive grants.",
            error_code="site_grant.user_inactive",
        )
    if site_id is not None:
        _validate_site(session, site_id)
    _check_not_duplicate(session, user, site_id)
    grant = SiteGrant(user_id=user.id, site_id=site_id)
    session.add(grant)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=grant.id,
        action="created",
        before=None,
        after=grant_snapshot(grant),
    )
    session.commit()
    return grant


def delete_site_grant(session: Session, actor: str, grant: SiteGrant) -> None:
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=grant.id,
        action="deleted",
        before=grant_snapshot(grant),
        after=None,
    )
    session.delete(grant)
    session.commit()
