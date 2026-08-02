"""Operational constraint services.

Constraints are created and retired, never edited. The enforcement of the
``mutual_exclusive_maintenance`` kind lives in the maintenance service,
next to the open-permit guard, so every path that starts an order passes
through it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..audit import record_audit
from ..domain import CONSTRAINT_TRANSITIONS, ConstraintKind, ConstraintStatus
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import Asset, Constraint, ConstraintMember

ENTITY_TYPE = "constraint"

MINIMUM_ENFORCED_MEMBERS = 2


def constraint_snapshot(constraint: Constraint) -> dict[str, Any]:
    return {
        "id": constraint.id,
        "kind": constraint.kind,
        "name": constraint.name,
        "description": constraint.description,
        "status": constraint.status,
        "asset_ids": [member.asset_id for member in constraint.members],
    }


def get_constraint(session: Session, constraint_id: str) -> Constraint:
    constraint = session.get(Constraint, constraint_id)
    if constraint is None:
        raise NotFoundError(
            f"Constraint {constraint_id} was not found.",
            error_code="constraint.not_found",
        )
    return constraint


def create_constraint(
    session: Session,
    actor: str,
    *,
    kind: ConstraintKind,
    name: str,
    description: str | None,
    asset_ids: list[str],
) -> Constraint:
    if len(set(asset_ids)) != len(asset_ids):
        raise RuleViolationError(
            "Constraint member assets must be distinct.",
            error_code="constraint.duplicate_member",
        )
    if kind is ConstraintKind.MUTUAL_EXCLUSIVE_MAINTENANCE and (
        len(asset_ids) < MINIMUM_ENFORCED_MEMBERS
    ):
        raise RuleViolationError(
            "A mutual_exclusive_maintenance constraint needs at least two member assets.",
            error_code="constraint.too_few_members",
        )
    cleaned_description = description.strip() if description is not None else None
    if kind is ConstraintKind.ADVISORY and not cleaned_description:
        raise RuleViolationError(
            "An advisory constraint carries its guidance as free text; a description is required.",
            error_code="constraint.description_required",
        )
    for asset_id in asset_ids:
        if session.get(Asset, asset_id) is None:
            raise RuleViolationError(
                f"Asset {asset_id} was not found.",
                error_code="constraint.asset_not_found",
            )
    constraint = Constraint(
        kind=kind.value,
        name=name,
        description=cleaned_description,
        status=ConstraintStatus.ACTIVE.value,
    )
    session.add(constraint)
    session.flush()
    for asset_id in asset_ids:
        session.add(ConstraintMember(constraint_id=constraint.id, asset_id=asset_id))
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=constraint.id,
        action="created",
        before=None,
        after=constraint_snapshot(constraint),
    )
    session.commit()
    return constraint


def retire_constraint(session: Session, actor: str, constraint: Constraint) -> Constraint:
    current = ConstraintStatus(constraint.status)
    allowed = CONSTRAINT_TRANSITIONS[current]
    if ConstraintStatus.RETIRED not in allowed:
        allowed_names = ", ".join(sorted(status.value for status in allowed)) or "none"
        raise ConflictError(
            f"Constraint status cannot change from {current.value!r} to 'retired'; "
            f"allowed: {allowed_names}.",
            error_code="constraint.invalid_transition",
        )
    constraint.status = ConstraintStatus.RETIRED.value
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=constraint.id,
        action="status_changed",
        before={"status": current.value},
        after={"status": ConstraintStatus.RETIRED.value},
    )
    session.commit()
    return constraint
