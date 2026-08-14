"""Maintenance order services."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session, aliased

from ..audit import jsonable, record_audit
from ..domain import (
    ORDER_TRANSITIONS,
    ConstraintKind,
    ConstraintStatus,
    MaintenanceOrderStatus,
    MaintenanceOrderType,
    WorkPermitStatus,
)
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import Asset, Constraint, ConstraintMember, MaintenanceOrder, WorkPermit

ENTITY_TYPE = "maintenance_order"

EDITABLE_STATUSES = frozenset({MaintenanceOrderStatus.DRAFT, MaintenanceOrderStatus.SCHEDULED})


def order_snapshot(order: MaintenanceOrder) -> dict[str, Any]:
    return {
        "id": order.id,
        "order_type": order.order_type,
        "status": order.status,
        "asset_id": order.asset_id,
        "title": order.title,
        "description": order.description,
        "due_date": order.due_date.isoformat(),
        "completion_notes": order.completion_notes,
    }


def _not_found(order_id: str) -> NotFoundError:
    return NotFoundError(
        f"Maintenance order {order_id} was not found.",
        error_code="maintenance_order.not_found",
    )


def get_order(session: Session, order_id: str) -> MaintenanceOrder:
    order = session.get(MaintenanceOrder, order_id)
    if order is None:
        raise _not_found(order_id)
    return order


def read_order(session: Session, order_id: str, readable: ColumnElement[bool]) -> MaintenanceOrder:
    """The order addressed by ``order_id``, if this request may read it.

    An order on a site the caller holds no grant for answers exactly what a
    missing one answers.
    """
    order = session.scalar(
        select(MaintenanceOrder).where(MaintenanceOrder.id == order_id, readable)
    )
    if order is None:
        raise _not_found(order_id)
    return order


def create_order(
    session: Session,
    actor: str,
    *,
    order_type: MaintenanceOrderType,
    asset_id: str,
    title: str,
    description: str | None,
    due_date: date,
) -> MaintenanceOrder:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise RuleViolationError(
            f"Asset {asset_id} was not found.",
            error_code="maintenance_order.asset_not_found",
        )
    order = MaintenanceOrder(
        order_type=order_type.value,
        status=MaintenanceOrderStatus.DRAFT.value,
        asset_id=asset_id,
        title=title,
        description=description,
        due_date=due_date,
    )
    session.add(order)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=order.id,
        action="created",
        before=None,
        after=order_snapshot(order),
    )
    session.commit()
    return order


def update_order(
    session: Session,
    actor: str,
    order: MaintenanceOrder,
    updates: dict[str, Any],
) -> MaintenanceOrder:
    if MaintenanceOrderStatus(order.status) not in EDITABLE_STATUSES:
        raise ConflictError(
            f"A maintenance order can only be edited while draft or scheduled; "
            f"this one is {order.status!r}.",
            error_code="maintenance_order.not_editable",
        )
    applied: dict[str, Any] = {}
    changes_before: dict[str, Any] = {}
    changes_after: dict[str, Any] = {}
    for field_name in ("title", "description", "due_date"):
        if field_name not in updates:
            continue
        old_value = getattr(order, field_name)
        new_value = updates[field_name]
        if old_value != new_value:
            applied[field_name] = new_value
            changes_before[field_name] = jsonable(old_value)
            changes_after[field_name] = jsonable(new_value)
    if not applied:
        return order
    for field_name, new_value in applied.items():
        setattr(order, field_name, new_value)
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=order.id,
        action="updated",
        before=changes_before,
        after=changes_after,
    )
    session.commit()
    return order


def _check_mutual_exclusion(session: Session, order: MaintenanceOrder) -> None:
    """Reject starting the order while a sibling constrained asset is under work.

    This guard sits next to the open-permit guard below: it looks for an
    active ``mutual_exclusive_maintenance`` constraint that contains the
    order's asset and another member asset with an order already in
    progress, and names both the constraint and the conflicting order.
    """
    sibling = aliased(ConstraintMember)
    row = session.execute(
        select(Constraint, MaintenanceOrder)
        .join(ConstraintMember, ConstraintMember.constraint_id == Constraint.id)
        .join(sibling, sibling.constraint_id == Constraint.id)
        .join(MaintenanceOrder, MaintenanceOrder.asset_id == sibling.asset_id)
        .where(
            Constraint.kind == ConstraintKind.MUTUAL_EXCLUSIVE_MAINTENANCE.value,
            Constraint.status == ConstraintStatus.ACTIVE.value,
            ConstraintMember.asset_id == order.asset_id,
            sibling.asset_id != order.asset_id,
            MaintenanceOrder.status == MaintenanceOrderStatus.IN_PROGRESS.value,
        )
        .order_by(MaintenanceOrder.created_at, MaintenanceOrder.id)
    ).first()
    if row is None:
        return
    constraint: Constraint = row[0]
    conflicting_order: MaintenanceOrder = row[1]
    raise ConflictError(
        f"Constraint {constraint.name!r} ({constraint.id}) allows only one of its member "
        f"assets under maintenance at a time; order {conflicting_order.id} "
        f"({conflicting_order.title!r}) is already in progress on asset "
        f"{conflicting_order.asset_id}.",
        error_code="maintenance_order.mutual_exclusion_conflict",
    )


def transition_order(
    session: Session,
    actor: str,
    order: MaintenanceOrder,
    to_status: MaintenanceOrderStatus,
    *,
    notes: str | None = None,
) -> MaintenanceOrder:
    current = MaintenanceOrderStatus(order.status)
    allowed = ORDER_TRANSITIONS[current]
    if to_status not in allowed:
        allowed_names = ", ".join(sorted(status.value for status in allowed)) or "none"
        raise ConflictError(
            f"Maintenance order status cannot change from {current.value!r} to "
            f"{to_status.value!r}; allowed: {allowed_names}.",
            error_code="maintenance_order.invalid_transition",
        )
    if to_status is MaintenanceOrderStatus.IN_PROGRESS:
        _check_mutual_exclusion(session, order)
    if to_status is MaintenanceOrderStatus.DONE:
        issued_permits = session.scalar(
            select(func.count())
            .select_from(WorkPermit)
            .where(
                WorkPermit.order_id == order.id,
                WorkPermit.status == WorkPermitStatus.ISSUED.value,
            )
        )
        if issued_permits:
            raise ConflictError(
                "The maintenance order still has an issued work permit open; "
                "close or revoke the permit before completing the order.",
                error_code="maintenance_order.open_permit",
            )
    cleaned_notes = notes.strip() if notes is not None else None
    if to_status is MaintenanceOrderStatus.DONE and not cleaned_notes:
        raise RuleViolationError(
            "Completion notes are required to complete a maintenance order.",
            error_code="maintenance_order.completion_notes_required",
        )
    before: dict[str, Any] = {"status": current.value}
    after: dict[str, Any] = {"status": to_status.value}
    if cleaned_notes:
        before["completion_notes"] = order.completion_notes
        after["completion_notes"] = cleaned_notes
        order.completion_notes = cleaned_notes
    order.status = to_status.value
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=order.id,
        action="status_changed",
        before=before,
        after=after,
    )
    session.commit()
    return order
