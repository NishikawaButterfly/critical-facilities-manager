"""Punch item services."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..domain import (
    PUNCH_ITEM_TRANSITIONS,
    PunchItemCategory,
    PunchItemSeverity,
    PunchItemStatus,
)
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import Asset, Location, PunchItem

ENTITY_TYPE = "punch_item"


def punch_item_snapshot(item: PunchItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "asset_id": item.asset_id,
        "location_id": item.location_id,
        "category": item.category,
        "severity": item.severity,
        "status": item.status,
        "title": item.title,
        "description": item.description,
        "due_date": item.due_date.isoformat() if item.due_date is not None else None,
        "started_by": item.started_by,
        "verified_by": item.verified_by,
        "closing_note": item.closing_note,
    }


def _not_found(punch_item_id: str) -> NotFoundError:
    return NotFoundError(
        f"Punch item {punch_item_id} was not found.",
        error_code="punch_item.not_found",
    )


def get_punch_item(session: Session, punch_item_id: str) -> PunchItem:
    item = session.get(PunchItem, punch_item_id)
    if item is None:
        raise _not_found(punch_item_id)
    return item


def read_punch_item(
    session: Session, punch_item_id: str, readable: ColumnElement[bool]
) -> PunchItem:
    """The punch item addressed by ``punch_item_id``, if this request may read it.

    An item on a site the caller holds no grant for — through its asset or
    through its location — answers exactly what a missing one answers.
    """
    item = session.scalar(select(PunchItem).where(PunchItem.id == punch_item_id, readable))
    if item is None:
        raise _not_found(punch_item_id)
    return item


def create_punch_item(
    session: Session,
    actor: str,
    *,
    asset_id: str | None,
    location_id: str | None,
    category: PunchItemCategory,
    severity: PunchItemSeverity,
    title: str,
    description: str | None,
    due_date: date | None,
) -> PunchItem:
    if (asset_id is None) == (location_id is None):
        raise RuleViolationError(
            "A punch item is scoped to exactly one of an asset or a location.",
            error_code="punch_item.exactly_one_scope",
        )
    if asset_id is not None and session.get(Asset, asset_id) is None:
        raise RuleViolationError(
            f"Asset {asset_id} was not found.",
            error_code="punch_item.asset_not_found",
        )
    if location_id is not None and session.get(Location, location_id) is None:
        raise RuleViolationError(
            f"Location {location_id} was not found.",
            error_code="punch_item.location_not_found",
        )
    item = PunchItem(
        asset_id=asset_id,
        location_id=location_id,
        category=category.value,
        severity=severity.value,
        status=PunchItemStatus.OPEN.value,
        title=title,
        description=description,
        due_date=due_date,
        started_by=None,
        verified_by=None,
        closing_note=None,
    )
    session.add(item)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=item.id,
        action="created",
        before=None,
        after=punch_item_snapshot(item),
    )
    session.commit()
    return item


def transition_punch_item(
    session: Session,
    actor: str,
    item: PunchItem,
    to_status: PunchItemStatus,
    *,
    closing_note: str | None = None,
) -> PunchItem:
    current = PunchItemStatus(item.status)
    allowed = PUNCH_ITEM_TRANSITIONS[current]
    if to_status not in allowed:
        allowed_names = ", ".join(sorted(status.value for status in allowed)) or "none"
        raise ConflictError(
            f"Punch item status cannot change from {current.value!r} to {to_status.value!r}; "
            f"allowed: {allowed_names}.",
            error_code="punch_item.invalid_transition",
        )
    before: dict[str, Any] = {"status": current.value}
    after: dict[str, Any] = {"status": to_status.value}
    if to_status is PunchItemStatus.IN_PROGRESS:
        before["started_by"] = item.started_by
        after["started_by"] = actor
        item.started_by = actor
    if to_status is PunchItemStatus.CLOSED:
        cleaned_note = closing_note.strip() if closing_note is not None else None
        if not cleaned_note:
            raise RuleViolationError(
                "A closing note is required to close a punch item.",
                error_code="punch_item.closing_note_required",
            )
        if actor == item.started_by:
            raise ConflictError(
                f"A punch item must be closed by someone other than whoever started the "
                f"work ({item.started_by!r}).",
                error_code="punch_item.verifier_must_differ",
            )
        before["verified_by"] = item.verified_by
        after["verified_by"] = actor
        before["closing_note"] = item.closing_note
        after["closing_note"] = cleaned_note
        item.verified_by = actor
        item.closing_note = cleaned_note
    item.status = to_status.value
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=item.id,
        action="status_changed",
        before=before,
        after=after,
    )
    session.commit()
    return item
