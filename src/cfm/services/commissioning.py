"""Commissioning test services.

Evidence is structured text only: each entry records a note, the acting
actor, and a UTC timestamp. File and photo evidence is future work pending
object storage.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from ..audit import record_audit
from ..domain import (
    COMMISSIONING_TRANSITIONS,
    CommissioningTestStatus,
    ProcedureStatus,
    PunchItemCategory,
    PunchItemSeverity,
    PunchItemStatus,
)
from ..errors import ConflictError, NotFoundError, RuleViolationError
from ..models import Asset, CommissioningEvidence, CommissioningTest, Procedure, PunchItem
from .punch_items import ENTITY_TYPE as PUNCH_ITEM_ENTITY_TYPE
from .punch_items import punch_item_snapshot
from .users import find_active_user

ENTITY_TYPE = "commissioning_test"


def test_snapshot(test: CommissioningTest) -> dict[str, Any]:
    return {
        "id": test.id,
        "asset_id": test.asset_id,
        "procedure_id": test.procedure_id,
        "title": test.title,
        "planned_date": test.planned_date.isoformat(),
        "status": test.status,
        "executed_by": test.executed_by,
        "witnessed_by": test.witnessed_by,
        "punch_item_id": test.punch_item_id,
    }


def get_test(session: Session, test_id: str) -> CommissioningTest:
    test = session.get(CommissioningTest, test_id)
    if test is None:
        raise NotFoundError(
            f"Commissioning test {test_id} was not found.",
            error_code="commissioning_test.not_found",
        )
    return test


def create_test(
    session: Session,
    actor: str,
    *,
    asset_id: str,
    procedure_id: str | None,
    title: str,
    planned_date: date,
) -> CommissioningTest:
    if session.get(Asset, asset_id) is None:
        raise RuleViolationError(
            f"Asset {asset_id} was not found.",
            error_code="commissioning_test.asset_not_found",
        )
    if procedure_id is not None:
        procedure = session.get(Procedure, procedure_id)
        if procedure is None:
            raise RuleViolationError(
                f"Procedure {procedure_id} was not found.",
                error_code="commissioning_test.procedure_not_found",
            )
        if ProcedureStatus(procedure.status) is not ProcedureStatus.APPROVED:
            raise RuleViolationError(
                f"A commissioning test can only reference an approved procedure; "
                f"procedure {procedure_id} is {procedure.status!r}.",
                error_code="commissioning_test.procedure_not_approved",
            )
    test = CommissioningTest(
        asset_id=asset_id,
        procedure_id=procedure_id,
        title=title,
        planned_date=planned_date,
        status=CommissioningTestStatus.PENDING.value,
        executed_by=None,
        witnessed_by=None,
        punch_item_id=None,
    )
    session.add(test)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=test.id,
        action="created",
        before=None,
        after=test_snapshot(test),
    )
    session.commit()
    return test


def _append_evidence(
    session: Session,
    actor: str,
    test: CommissioningTest,
    note: str,
) -> CommissioningEvidence:
    """Append one evidence note and audit it. The caller owns the transaction."""
    entry = CommissioningEvidence(test_id=test.id, note=note, actor=actor)
    session.add(entry)
    session.flush()
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=test.id,
        action="evidence_added",
        before=None,
        after={"evidence_id": entry.id, "note": note},
    )
    return entry


def add_evidence(
    session: Session,
    actor: str,
    test: CommissioningTest,
    note: str,
) -> CommissioningTest:
    if CommissioningTestStatus(test.status) is not CommissioningTestStatus.PENDING:
        raise ConflictError(
            f"Evidence can only be appended while the test is pending; this one is "
            f"{test.status!r}.",
            error_code="commissioning_test.not_pending",
        )
    cleaned_note = note.strip()
    if not cleaned_note:
        raise RuleViolationError(
            "An evidence entry needs non-empty note text.",
            error_code="commissioning_test.evidence_note_required",
        )
    _append_evidence(session, actor, test, cleaned_note)
    session.commit()
    return test


def record_result(
    session: Session,
    actor: str,
    test: CommissioningTest,
    outcome: CommissioningTestStatus,
    *,
    witness: str,
    evidence_note: str | None = None,
) -> CommissioningTest:
    current = CommissioningTestStatus(test.status)
    allowed = COMMISSIONING_TRANSITIONS[current]
    if outcome not in allowed:
        allowed_names = ", ".join(sorted(status.value for status in allowed)) or "none"
        raise ConflictError(
            f"Commissioning test status cannot change from {current.value!r} to "
            f"{outcome.value!r}; allowed: {allowed_names}.",
            error_code="commissioning_test.invalid_transition",
        )
    cleaned_witness = witness.strip()
    if not cleaned_witness:
        raise RuleViolationError(
            "A witness is required to record a commissioning test result.",
            error_code="commissioning_test.witness_required",
        )
    if cleaned_witness == actor:
        raise ConflictError(
            f"A commissioning test must be witnessed by someone other than its executor "
            f"({actor!r}).",
            error_code="commissioning_test.witness_must_differ",
        )
    if find_active_user(session, cleaned_witness) is None:
        raise RuleViolationError(
            f"The witness must be an active user; {cleaned_witness!r} is not.",
            error_code="commissioning_test.witness_not_active_user",
        )
    cleaned_note = evidence_note.strip() if evidence_note is not None else None
    if cleaned_note:
        _append_evidence(session, actor, test, cleaned_note)
    test.status = outcome.value
    test.executed_by = actor
    test.witnessed_by = cleaned_witness
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=test.id,
        action="status_changed",
        before={"status": current.value, "executed_by": None, "witnessed_by": None},
        after={
            "status": outcome.value,
            "executed_by": actor,
            "witnessed_by": cleaned_witness,
        },
    )
    session.commit()
    return test


def create_punch_item_from_test(
    session: Session,
    actor: str,
    test: CommissioningTest,
    *,
    category: PunchItemCategory,
    severity: PunchItemSeverity,
    title: str | None,
    description: str | None,
    due_date: date | None,
) -> PunchItem:
    if CommissioningTestStatus(test.status) is not CommissioningTestStatus.FAILED:
        raise ConflictError(
            f"A punch item can only be opened from a failed commissioning test; "
            f"this one is {test.status!r}.",
            error_code="commissioning_test.not_failed",
        )
    if test.punch_item_id is not None:
        raise ConflictError(
            f"Commissioning test {test.id} is already linked to punch item {test.punch_item_id}.",
            error_code="commissioning_test.punch_item_exists",
        )
    item = PunchItem(
        asset_id=test.asset_id,
        location_id=None,
        category=category.value,
        severity=severity.value,
        status=PunchItemStatus.OPEN.value,
        title=title or test.title,
        description=description,
        due_date=due_date,
        started_by=None,
        verified_by=None,
        closing_note=None,
    )
    session.add(item)
    session.flush()
    test.punch_item_id = item.id
    record_audit(
        session,
        actor=actor,
        entity_type=PUNCH_ITEM_ENTITY_TYPE,
        entity_id=item.id,
        action="created",
        before=None,
        after=punch_item_snapshot(item),
    )
    record_audit(
        session,
        actor=actor,
        entity_type=PUNCH_ITEM_ENTITY_TYPE,
        entity_id=item.id,
        action="created_from_commissioning_test",
        before=None,
        after={"commissioning_test_id": test.id},
    )
    record_audit(
        session,
        actor=actor,
        entity_type=ENTITY_TYPE,
        entity_id=test.id,
        action="punch_item_created",
        before={"punch_item_id": None},
        after={"punch_item_id": item.id},
    )
    session.commit()
    return item
