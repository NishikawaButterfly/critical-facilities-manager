"""Asset inventory services."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..domain import ASSET_TRANSITIONS, AssetCriticality, AssetStatus
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import Asset, Location
from .locations import resolve_site_id

ENTITY_TYPE = "asset"


def asset_snapshot(asset: Asset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "tag": asset.tag,
        "name": asset.name,
        "asset_type": asset.asset_type,
        "criticality": asset.criticality,
        "status": asset.status,
        "location_id": asset.location_id,
        "site_id": asset.site_id,
    }


def _not_found(asset_id: str) -> NotFoundError:
    return NotFoundError(f"Asset {asset_id} was not found.", error_code="asset.not_found")


def get_asset(session: Session, asset_id: str) -> Asset:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise _not_found(asset_id)
    return asset


def read_asset(session: Session, asset_id: str, readable: ColumnElement[bool]) -> Asset:
    """The asset addressed by ``asset_id``, if this request may read it.

    An asset outside the caller's grants answers exactly what a missing one
    answers, down to the message: the existence of another site's asset is
    itself information the caller has no grant for.
    """
    asset = session.scalar(select(Asset).where(Asset.id == asset_id, readable))
    if asset is None:
        raise _not_found(asset_id)
    return asset


def _check_tag_free(
    session: Session,
    *,
    site_id: str,
    tag: str,
    exclude_id: str | None = None,
) -> None:
    conditions = [Asset.site_id == site_id, Asset.tag == tag]
    if exclude_id is not None:
        conditions.append(Asset.id != exclude_id)
    duplicate_count = session.scalar(select(func.count()).select_from(Asset).where(*conditions))
    if duplicate_count:
        raise ConflictError(
            f"Asset tag {tag!r} is already used within the same site.",
            error_code="asset.duplicate_tag",
        )


def create_asset(
    session: Session,
    actor: str,
    *,
    tag: str,
    name: str,
    asset_type: str,
    criticality: AssetCriticality,
    location_id: str,
    status: AssetStatus,
) -> Asset:
    location = session.get(Location, location_id)
    if location is None:
        raise RuleViolationError(
            f"Location {location_id} was not found.",
            error_code="asset.location_not_found",
        )
    site_id = resolve_site_id(session, location)
    _check_tag_free(session, site_id=site_id, tag=tag)
    asset = Asset(
        tag=tag,
        name=name,
        asset_type=asset_type,
        criticality=criticality.value,
        status=status.value,
        location_id=location_id,
        site_id=site_id,
    )
    session.add(asset)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=asset.id,
        action="created",
        before=None,
        after=asset_snapshot(asset),
    )
    session.commit()
    return asset


def update_asset(
    session: Session,
    actor: str,
    asset: Asset,
    updates: dict[str, Any],
) -> Asset:
    changes_before: dict[str, Any] = {}
    changes_after: dict[str, Any] = {}

    def stage(field_name: str, new_value: Any) -> None:
        old_value = getattr(asset, field_name)
        if old_value != new_value:
            changes_before[field_name] = old_value
            changes_after[field_name] = new_value

    if "location_id" in updates and updates["location_id"] != asset.location_id:
        new_location = session.get(Location, updates["location_id"])
        if new_location is None:
            raise RuleViolationError(
                f"Location {updates['location_id']} was not found.",
                error_code="asset.location_not_found",
            )
        stage("location_id", new_location.id)
        stage("site_id", resolve_site_id(session, new_location))
    for field_name in ("tag", "name", "asset_type"):
        if field_name in updates:
            stage(field_name, updates[field_name])
    if "criticality" in updates:
        stage("criticality", AssetCriticality(updates["criticality"]).value)
    if not changes_after:
        return asset
    if "site_id" in changes_after or "tag" in changes_after:
        _check_tag_free(
            session,
            site_id=changes_after.get("site_id", asset.site_id),
            tag=changes_after.get("tag", asset.tag),
            exclude_id=asset.id,
        )
    for field_name, new_value in changes_after.items():
        setattr(asset, field_name, new_value)
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=asset.id,
        action="updated",
        before=changes_before,
        after=changes_after,
    )
    session.commit()
    return asset


def transition_asset(
    session: Session,
    actor: str,
    asset: Asset,
    to_status: AssetStatus,
) -> Asset:
    current = AssetStatus(asset.status)
    allowed = ASSET_TRANSITIONS[current]
    if to_status not in allowed:
        allowed_names = ", ".join(sorted(status.value for status in allowed)) or "none"
        raise ConflictError(
            f"Asset status cannot change from {current.value!r} to {to_status.value!r}; "
            f"allowed: {allowed_names}.",
            error_code="asset.invalid_transition",
        )
    asset.status = to_status.value
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=asset.id,
        action="status_changed",
        before={"status": current.value},
        after={"status": to_status.value},
    )
    session.commit()
    return asset
