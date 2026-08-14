"""Work permit services."""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..domain import (
    PERMIT_TRANSITIONS,
    MaintenanceOrderStatus,
    ProcedureStatus,
    WorkPermitStatus,
)
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import MaintenanceOrder, Procedure, WorkPermit

ENTITY_TYPE = "work_permit"

PERMITTABLE_ORDER_STATUSES = frozenset(
    {MaintenanceOrderStatus.SCHEDULED, MaintenanceOrderStatus.IN_PROGRESS}
)


def permit_snapshot(permit: WorkPermit) -> dict[str, Any]:
    return {
        "id": permit.id,
        "order_id": permit.order_id,
        "procedure_id": permit.procedure_id,
        "scope": permit.scope,
        "status": permit.status,
        "requested_by": permit.requested_by,
        "issued_by": permit.issued_by,
        "completion_note": permit.completion_note,
    }


def _not_found(permit_id: str) -> NotFoundError:
    return NotFoundError(
        f"Work permit {permit_id} was not found.",
        error_code="work_permit.not_found",
    )


def get_permit(session: Session, permit_id: str) -> WorkPermit:
    permit = session.get(WorkPermit, permit_id)
    if permit is None:
        raise _not_found(permit_id)
    return permit


def read_permit(session: Session, permit_id: str, readable: ColumnElement[bool]) -> WorkPermit:
    """The permit addressed by ``permit_id``, if this request may read it.

    A permit on a site the caller holds no grant for answers exactly what a
    missing one answers.
    """
    permit = session.scalar(select(WorkPermit).where(WorkPermit.id == permit_id, readable))
    if permit is None:
        raise _not_found(permit_id)
    return permit


def create_permit(
    session: Session,
    actor: str,
    *,
    order_id: str,
    procedure_id: str | None,
    scope: str,
) -> WorkPermit:
    order = session.get(MaintenanceOrder, order_id)
    if order is None:
        raise RuleViolationError(
            f"Maintenance order {order_id} was not found.",
            error_code="work_permit.order_not_found",
        )
    if MaintenanceOrderStatus(order.status) not in PERMITTABLE_ORDER_STATUSES:
        raise RuleViolationError(
            f"A work permit requires a scheduled or in_progress maintenance order; "
            f"order {order_id} is {order.status!r}.",
            error_code="work_permit.order_not_active",
        )
    if procedure_id is not None:
        procedure = session.get(Procedure, procedure_id)
        if procedure is None:
            raise RuleViolationError(
                f"Procedure {procedure_id} was not found.",
                error_code="work_permit.procedure_not_found",
            )
        if ProcedureStatus(procedure.status) is not ProcedureStatus.APPROVED:
            raise RuleViolationError(
                f"A work permit can only reference an approved procedure; "
                f"procedure {procedure_id} is {procedure.status!r}.",
                error_code="work_permit.procedure_not_approved",
            )
    permit = WorkPermit(
        order_id=order_id,
        procedure_id=procedure_id,
        scope=scope,
        status=WorkPermitStatus.REQUESTED.value,
        requested_by=actor,
        issued_by=None,
        completion_note=None,
    )
    session.add(permit)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=permit.id,
        action="created",
        before=None,
        after=permit_snapshot(permit),
    )
    session.commit()
    return permit


def transition_permit(
    session: Session,
    actor: str,
    permit: WorkPermit,
    to_status: WorkPermitStatus,
    *,
    note: str | None = None,
) -> WorkPermit:
    current = WorkPermitStatus(permit.status)
    allowed = PERMIT_TRANSITIONS[current]
    if to_status not in allowed:
        allowed_names = ", ".join(sorted(status.value for status in allowed)) or "none"
        raise ConflictError(
            f"Work permit status cannot change from {current.value!r} to {to_status.value!r}; "
            f"allowed: {allowed_names}.",
            error_code="work_permit.invalid_transition",
        )
    cleaned_note = note.strip() if note is not None else None
    if to_status is WorkPermitStatus.CLOSED and not cleaned_note:
        raise RuleViolationError(
            "A completion note is required to close a work permit.",
            error_code="work_permit.completion_note_required",
        )
    before: dict[str, Any] = {"status": current.value}
    after: dict[str, Any] = {"status": to_status.value}
    if to_status is WorkPermitStatus.ISSUED:
        if actor == permit.requested_by:
            raise ConflictError(
                f"A work permit must be issued by someone other than its requester "
                f"({permit.requested_by!r}).",
                error_code="work_permit.issuer_must_differ",
            )
        before["issued_by"] = permit.issued_by
        after["issued_by"] = actor
        permit.issued_by = actor
    if cleaned_note:
        before["completion_note"] = permit.completion_note
        after["completion_note"] = cleaned_note
        permit.completion_note = cleaned_note
    permit.status = to_status.value
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=permit.id,
        action="status_changed",
        before=before,
        after=after,
    )
    session.commit()
    return permit
