"""Incident services."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from ..audit import record_audit
from ..domain import (
    INCIDENT_TRANSITIONS,
    IncidentSeverity,
    IncidentStatus,
    MaintenanceOrderStatus,
    MaintenanceOrderType,
)
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import Asset, Incident, MaintenanceOrder
from .maintenance import ENTITY_TYPE as ORDER_ENTITY_TYPE
from .maintenance import order_snapshot

ENTITY_TYPE = "incident"

ACTIVE_STATUSES = frozenset({IncidentStatus.OPEN, IncidentStatus.INVESTIGATING})


def incident_snapshot(incident: Incident) -> dict[str, Any]:
    return {
        "id": incident.id,
        "asset_id": incident.asset_id,
        "severity": incident.severity,
        "status": incident.status,
        "title": incident.title,
        "description": incident.description,
        "resolution_notes": incident.resolution_notes,
        "corrective_order_id": incident.corrective_order_id,
    }


def get_incident(session: Session, incident_id: str) -> Incident:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise NotFoundError(
            f"Incident {incident_id} was not found.",
            error_code="incident.not_found",
        )
    return incident


def create_incident(
    session: Session,
    actor: str,
    *,
    asset_id: str,
    severity: IncidentSeverity,
    title: str,
    description: str | None,
) -> Incident:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise RuleViolationError(
            f"Asset {asset_id} was not found.",
            error_code="incident.asset_not_found",
        )
    incident = Incident(
        asset_id=asset_id,
        severity=severity.value,
        status=IncidentStatus.OPEN.value,
        title=title,
        description=description,
        resolution_notes=None,
        corrective_order_id=None,
    )
    session.add(incident)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=incident.id,
        action="created",
        before=None,
        after=incident_snapshot(incident),
    )
    session.commit()
    return incident


def transition_incident(
    session: Session,
    actor: str,
    incident: Incident,
    to_status: IncidentStatus,
    *,
    notes: str | None = None,
) -> Incident:
    current = IncidentStatus(incident.status)
    allowed = INCIDENT_TRANSITIONS[current]
    if to_status not in allowed:
        allowed_names = ", ".join(sorted(status.value for status in allowed)) or "none"
        raise ConflictError(
            f"Incident status cannot change from {current.value!r} to {to_status.value!r}; "
            f"allowed: {allowed_names}.",
            error_code="incident.invalid_transition",
        )
    cleaned_notes = notes.strip() if notes is not None else None
    if to_status is IncidentStatus.RESOLVED and not cleaned_notes:
        raise RuleViolationError(
            "Resolution notes are required to resolve an incident.",
            error_code="incident.resolution_notes_required",
        )
    before: dict[str, Any] = {"status": current.value}
    after: dict[str, Any] = {"status": to_status.value}
    if cleaned_notes:
        before["resolution_notes"] = incident.resolution_notes
        after["resolution_notes"] = cleaned_notes
        incident.resolution_notes = cleaned_notes
    incident.status = to_status.value
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=incident.id,
        action="status_changed",
        before=before,
        after=after,
    )
    session.commit()
    return incident


def create_corrective_order(
    session: Session,
    actor: str,
    incident: Incident,
    *,
    due_date: date,
    title: str | None,
    description: str | None,
) -> MaintenanceOrder:
    if IncidentStatus(incident.status) not in ACTIVE_STATUSES:
        raise ConflictError(
            f"A corrective order can only be created while the incident is open or "
            f"investigating; this one is {incident.status!r}.",
            error_code="incident.not_active",
        )
    if incident.corrective_order_id is not None:
        raise ConflictError(
            f"Incident {incident.id} is already linked to corrective order "
            f"{incident.corrective_order_id}.",
            error_code="incident.corrective_order_exists",
        )
    order = MaintenanceOrder(
        order_type=MaintenanceOrderType.CORRECTIVE.value,
        status=MaintenanceOrderStatus.DRAFT.value,
        asset_id=incident.asset_id,
        title=title or incident.title,
        description=description or incident.description,
        due_date=due_date,
    )
    session.add(order)
    session.flush()
    incident.corrective_order_id = order.id
    record_audit(
        session,
        actor=actor,
        entity_type=ORDER_ENTITY_TYPE,
        entity_id=order.id,
        action="created",
        before=None,
        after=order_snapshot(order),
    )
    record_audit(
        session,
        actor=actor,
        entity_type=ORDER_ENTITY_TYPE,
        entity_id=order.id,
        action="created_from_incident",
        before=None,
        after={"incident_id": incident.id},
    )
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=incident.id,
        action="corrective_order_created",
        before={"corrective_order_id": None},
        after={"corrective_order_id": order.id},
    )
    session.commit()
    return order
