"""Commissioning test endpoints. Result recording is an explicit POST action."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..domain import CommissioningTestStatus
from ..models import CommissioningTest
from ..schemas import (
    CommissioningEvidenceCreate,
    CommissioningTestCreate,
    CommissioningTestPunchItem,
    CommissioningTestRecord,
    CommissioningTestResponse,
    Page,
    PunchItemResponse,
)
from ..services.commissioning import (
    add_evidence,
    create_punch_item_from_test,
    create_test,
    get_test,
    record_result,
)
from .deps import ActorDep, PageDep, SessionDep
from .serializers import commissioning_test_response, punch_item_response

router = APIRouter(prefix="/commissioning-tests", tags=["commissioning-tests"])


@router.post("", response_model=CommissioningTestResponse, status_code=201)
def create_test_endpoint(
    payload: CommissioningTestCreate,
    session: SessionDep,
    actor: ActorDep,
) -> CommissioningTestResponse:
    test = create_test(
        session,
        actor,
        asset_id=payload.asset_id,
        procedure_id=payload.procedure_id,
        title=payload.title,
        planned_date=payload.planned_date,
    )
    return commissioning_test_response(test)


@router.get("", response_model=Page[CommissioningTestResponse])
def list_tests(
    session: SessionDep,
    page: PageDep,
    asset_id: Annotated[str | None, Query()] = None,
    status: Annotated[CommissioningTestStatus | None, Query()] = None,
) -> Page[CommissioningTestResponse]:
    query = select(CommissioningTest)
    count_query = select(func.count()).select_from(CommissioningTest)
    if asset_id is not None:
        query = query.where(CommissioningTest.asset_id == asset_id)
        count_query = count_query.where(CommissioningTest.asset_id == asset_id)
    if status is not None:
        query = query.where(CommissioningTest.status == status.value)
        count_query = count_query.where(CommissioningTest.status == status.value)
    total = session.scalar(count_query) or 0
    tests = session.scalars(
        query.order_by(CommissioningTest.created_at, CommissioningTest.id)
        .offset(page.offset)
        .limit(page.limit)
    ).all()
    return Page[CommissioningTestResponse](
        items=[commissioning_test_response(test) for test in tests],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{test_id}", response_model=CommissioningTestResponse)
def get_test_endpoint(test_id: str, session: SessionDep) -> CommissioningTestResponse:
    return commissioning_test_response(get_test(session, test_id))


@router.post("/{test_id}/evidence", response_model=CommissioningTestResponse, status_code=201)
def add_evidence_endpoint(
    test_id: str,
    payload: CommissioningEvidenceCreate,
    session: SessionDep,
    actor: ActorDep,
) -> CommissioningTestResponse:
    test = get_test(session, test_id)
    return commissioning_test_response(add_evidence(session, actor, test, payload.note))


@router.post("/{test_id}/pass", response_model=CommissioningTestResponse)
def record_pass(
    test_id: str,
    payload: CommissioningTestRecord,
    session: SessionDep,
    actor: ActorDep,
) -> CommissioningTestResponse:
    test = get_test(session, test_id)
    return commissioning_test_response(
        record_result(
            session,
            actor,
            test,
            CommissioningTestStatus.PASSED,
            witness=payload.witness,
            evidence_note=payload.evidence_note,
        )
    )


@router.post("/{test_id}/fail", response_model=CommissioningTestResponse)
def record_fail(
    test_id: str,
    payload: CommissioningTestRecord,
    session: SessionDep,
    actor: ActorDep,
) -> CommissioningTestResponse:
    test = get_test(session, test_id)
    return commissioning_test_response(
        record_result(
            session,
            actor,
            test,
            CommissioningTestStatus.FAILED,
            witness=payload.witness,
            evidence_note=payload.evidence_note,
        )
    )


@router.post("/{test_id}/punch-item", response_model=PunchItemResponse, status_code=201)
def create_punch_item_endpoint(
    test_id: str,
    payload: CommissioningTestPunchItem,
    session: SessionDep,
    actor: ActorDep,
) -> PunchItemResponse:
    test = get_test(session, test_id)
    item = create_punch_item_from_test(
        session,
        actor,
        test,
        category=payload.category,
        severity=payload.severity,
        title=payload.title,
        description=payload.description,
        due_date=payload.due_date,
    )
    return punch_item_response(item)
