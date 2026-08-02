"""Procedure (MOP/SOP/EOP) services."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..domain import PROCEDURE_TRANSITIONS, ProcedureKind, ProcedureStatus
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import Procedure

ENTITY_TYPE = "procedure"


def procedure_snapshot(procedure: Procedure) -> dict[str, Any]:
    return {
        "id": procedure.id,
        "kind": procedure.kind,
        "status": procedure.status,
        "title": procedure.title,
        "steps": list(procedure.steps),
        "version": procedure.version,
        "predecessor_id": procedure.predecessor_id,
        "last_edited_by": procedure.last_edited_by,
        "approved_by": procedure.approved_by,
    }


def get_procedure(session: Session, procedure_id: str) -> Procedure:
    procedure = session.get(Procedure, procedure_id)
    if procedure is None:
        raise NotFoundError(
            f"Procedure {procedure_id} was not found.",
            error_code="procedure.not_found",
        )
    return procedure


def clean_steps(steps: list[str]) -> list[str]:
    """Normalize a step list, rejecting steps whose text is blank."""
    cleaned = [step.strip() for step in steps]
    if not cleaned or any(not step for step in cleaned):
        raise RuleViolationError(
            "Every procedure step needs non-empty text.",
            error_code="procedure.step_text_required",
        )
    return cleaned


def _check_transition(procedure: Procedure, to_status: ProcedureStatus) -> ProcedureStatus:
    current = ProcedureStatus(procedure.status)
    allowed = PROCEDURE_TRANSITIONS[current]
    if to_status not in allowed:
        allowed_names = ", ".join(sorted(status.value for status in allowed)) or "none"
        raise ConflictError(
            f"Procedure status cannot change from {current.value!r} to {to_status.value!r}; "
            f"allowed: {allowed_names}.",
            error_code="procedure.invalid_transition",
        )
    return current


def create_procedure(
    session: Session,
    actor: str,
    *,
    kind: ProcedureKind,
    title: str,
    steps: list[str],
) -> Procedure:
    procedure = Procedure(
        kind=kind.value,
        status=ProcedureStatus.DRAFT.value,
        title=title,
        steps=clean_steps(steps),
        version=1,
        predecessor_id=None,
        last_edited_by=actor,
        approved_by=None,
    )
    session.add(procedure)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=procedure.id,
        action="created",
        before=None,
        after=procedure_snapshot(procedure),
    )
    session.commit()
    return procedure


def update_procedure(
    session: Session,
    actor: str,
    procedure: Procedure,
    updates: dict[str, Any],
) -> Procedure:
    if ProcedureStatus(procedure.status) is not ProcedureStatus.DRAFT:
        raise ConflictError(
            f"A procedure can only be edited while draft; this one is {procedure.status!r}. "
            "Start a new version to change an approved procedure.",
            error_code="procedure.not_editable",
        )
    changes_before: dict[str, Any] = {}
    changes_after: dict[str, Any] = {}
    if "title" in updates and updates["title"] != procedure.title:
        changes_before["title"] = procedure.title
        changes_after["title"] = updates["title"]
    if "steps" in updates:
        new_steps = clean_steps(updates["steps"])
        if new_steps != list(procedure.steps):
            changes_before["steps"] = list(procedure.steps)
            changes_after["steps"] = new_steps
    if not changes_after:
        return procedure
    for field_name, new_value in changes_after.items():
        setattr(procedure, field_name, new_value)
    procedure.last_edited_by = actor
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=procedure.id,
        action="updated",
        before=changes_before,
        after=changes_after,
    )
    session.commit()
    return procedure


def approve_procedure(session: Session, actor: str, procedure: Procedure) -> Procedure:
    current = _check_transition(procedure, ProcedureStatus.APPROVED)
    if actor == procedure.last_edited_by:
        raise ConflictError(
            "A procedure must be approved by someone other than the author of its "
            f"last draft edit ({procedure.last_edited_by!r}).",
            error_code="procedure.approver_must_differ",
        )
    procedure.status = ProcedureStatus.APPROVED.value
    procedure.approved_by = actor
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=procedure.id,
        action="status_changed",
        before={"status": current.value, "approved_by": None},
        after={"status": ProcedureStatus.APPROVED.value, "approved_by": actor},
    )
    session.commit()
    return procedure


def retire_procedure(session: Session, actor: str, procedure: Procedure) -> Procedure:
    current = _check_transition(procedure, ProcedureStatus.RETIRED)
    procedure.status = ProcedureStatus.RETIRED.value
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=procedure.id,
        action="status_changed",
        before={"status": current.value},
        after={"status": ProcedureStatus.RETIRED.value},
    )
    session.commit()
    return procedure


def create_new_version(session: Session, actor: str, procedure: Procedure) -> Procedure:
    if ProcedureStatus(procedure.status) is not ProcedureStatus.APPROVED:
        raise ConflictError(
            f"Only an approved procedure can start a new version; this one is "
            f"{procedure.status!r}.",
            error_code="procedure.not_approved",
        )
    successor = session.scalar(select(Procedure).where(Procedure.predecessor_id == procedure.id))
    if successor is not None:
        raise ConflictError(
            f"Procedure {procedure.id} already has a successor (version {successor.version}).",
            error_code="procedure.successor_exists",
        )
    new_version = Procedure(
        kind=procedure.kind,
        status=ProcedureStatus.DRAFT.value,
        title=procedure.title,
        steps=list(procedure.steps),
        version=procedure.version + 1,
        predecessor_id=procedure.id,
        last_edited_by=actor,
        approved_by=None,
    )
    session.add(new_version)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=new_version.id,
        action="created",
        before=None,
        after=procedure_snapshot(new_version),
    )
    session.commit()
    return new_version


def get_version_chain(session: Session, procedure: Procedure) -> list[Procedure]:
    """Return the full version chain of a procedure, oldest version first."""
    root = procedure
    while root.predecessor_id is not None:
        predecessor = session.get(Procedure, root.predecessor_id)
        if predecessor is None:  # pragma: no cover - foreign keys keep predecessors present
            raise NotFoundError(
                f"Predecessor procedure {root.predecessor_id} was not found.",
                error_code="procedure.predecessor_not_found",
            )
        root = predecessor
    chain = [root]
    while True:
        successor = session.scalar(
            select(Procedure).where(Procedure.predecessor_id == chain[-1].id)
        )
        if successor is None:
            return chain
        chain.append(successor)
