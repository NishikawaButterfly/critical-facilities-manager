"""Location hierarchy services."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..domain import LOCATION_PARENT_KIND, LocationKind
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import Asset, Location, SiteGrant

ENTITY_TYPE = "location"


def location_snapshot(location: Location) -> dict[str, Any]:
    return {
        "id": location.id,
        "kind": location.kind,
        "code": location.code,
        "name": location.name,
        "parent_id": location.parent_id,
    }


def get_location(session: Session, location_id: str) -> Location:
    location = session.get(Location, location_id)
    if location is None:
        raise NotFoundError(
            f"Location {location_id} was not found.", error_code="location.not_found"
        )
    return location


def resolve_site_id(session: Session, location: Location) -> str:
    """Walk up the hierarchy to the site a location ultimately belongs to."""
    current = location
    while current.parent_id is not None:
        parent = session.get(Location, current.parent_id)
        if parent is None:  # pragma: no cover - foreign keys keep parents present
            raise NotFoundError(
                f"Parent location {current.parent_id} was not found.",
                error_code="location.parent_not_found",
            )
        current = parent
    return current.id


def _check_code_free(
    session: Session,
    *,
    parent_id: str | None,
    code: str,
    exclude_id: str | None = None,
) -> None:
    conditions = [Location.code == code]
    if parent_id is None:
        conditions.append(Location.parent_id.is_(None))
    else:
        conditions.append(Location.parent_id == parent_id)
    if exclude_id is not None:
        conditions.append(Location.id != exclude_id)
    duplicate_count = session.scalar(select(func.count()).select_from(Location).where(*conditions))
    if duplicate_count:
        scope = "at the top level" if parent_id is None else "under the same parent"
        raise ConflictError(
            f"Location code {code!r} already exists {scope}.",
            error_code="location.duplicate_code",
        )


def create_location(
    session: Session,
    actor: str,
    *,
    kind: LocationKind,
    code: str,
    name: str,
    parent_id: str | None,
) -> Location:
    expected_parent = LOCATION_PARENT_KIND[kind]
    if expected_parent is None:
        if parent_id is not None:
            raise RuleViolationError(
                "A site must not have a parent location.",
                error_code="location.invalid_parent_kind",
            )
    else:
        if parent_id is None:
            raise RuleViolationError(
                f"A {kind.value} requires a {expected_parent.value} parent.",
                error_code="location.parent_required",
            )
        parent = session.get(Location, parent_id)
        if parent is None:
            raise RuleViolationError(
                f"Parent location {parent_id} was not found.",
                error_code="location.parent_not_found",
            )
        if parent.kind != expected_parent.value:
            raise RuleViolationError(
                f"A {kind.value} must be created under a {expected_parent.value}; "
                f"{parent.code!r} is a {parent.kind}.",
                error_code="location.invalid_parent_kind",
            )
    _check_code_free(session, parent_id=parent_id, code=code)
    location = Location(kind=kind.value, code=code, name=name, parent_id=parent_id)
    session.add(location)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=location.id,
        action="created",
        before=None,
        after=location_snapshot(location),
    )
    session.commit()
    return location


def update_location(
    session: Session,
    actor: str,
    location: Location,
    updates: dict[str, Any],
) -> Location:
    changes_before: dict[str, Any] = {}
    changes_after: dict[str, Any] = {}
    for field_name in ("code", "name"):
        if field_name not in updates:
            continue
        old_value = getattr(location, field_name)
        new_value = updates[field_name]
        if old_value != new_value:
            changes_before[field_name] = old_value
            changes_after[field_name] = new_value
    if not changes_after:
        return location
    if "code" in changes_after:
        _check_code_free(
            session,
            parent_id=location.parent_id,
            code=changes_after["code"],
            exclude_id=location.id,
        )
    for field_name, new_value in changes_after.items():
        setattr(location, field_name, new_value)
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=location.id,
        action="updated",
        before=changes_before,
        after=changes_after,
    )
    session.commit()
    return location


def delete_location(session: Session, actor: str, location: Location) -> None:
    child_count = session.scalar(
        select(func.count()).select_from(Location).where(Location.parent_id == location.id)
    )
    if child_count:
        raise ConflictError(
            f"Location {location.code!r} still has child locations.",
            error_code="location.has_children",
        )
    asset_count = session.scalar(
        select(func.count()).select_from(Asset).where(Asset.location_id == location.id)
    )
    if asset_count:
        raise ConflictError(
            f"Location {location.code!r} still has assets assigned to it.",
            error_code="location.has_assets",
        )
    grant_count = session.scalar(
        select(func.count()).select_from(SiteGrant).where(SiteGrant.site_id == location.id)
    )
    if grant_count:
        raise ConflictError(
            f"Location {location.code!r} is still referenced by site grants; "
            "delete those grants first.",
            error_code="location.has_site_grants",
        )
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=location.id,
        action="deleted",
        before=location_snapshot(location),
        after=None,
    )
    session.delete(location)
    session.commit()
