"""Work permit endpoints. Transitions are explicit POST actions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..authz import site_of_order_id
from ..domain import WorkPermitStatus
from ..models import WorkPermit
from ..schemas import (
    Page,
    WorkPermitClose,
    WorkPermitCreate,
    WorkPermitResponse,
    WorkPermitRevoke,
)
from ..services.permits import create_permit, get_permit, transition_permit
from .deps import ActorDep, PageDep, SessionDep, SiteAccessDep
from .serializers import work_permit_response

router = APIRouter(prefix="/work-permits", tags=["work-permits"])


@router.post("", response_model=WorkPermitResponse, status_code=201)
def create_permit_endpoint(
    payload: WorkPermitCreate,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> WorkPermitResponse:
    access.require_site(site_of_order_id(session, payload.order_id))
    permit = create_permit(
        session,
        actor,
        order_id=payload.order_id,
        procedure_id=payload.procedure_id,
        scope=payload.scope,
    )
    return work_permit_response(permit)


@router.get("", response_model=Page[WorkPermitResponse])
def list_permits(
    session: SessionDep,
    page: PageDep,
    order_id: Annotated[str | None, Query()] = None,
    status: Annotated[WorkPermitStatus | None, Query()] = None,
) -> Page[WorkPermitResponse]:
    query = select(WorkPermit)
    count_query = select(func.count()).select_from(WorkPermit)
    if order_id is not None:
        query = query.where(WorkPermit.order_id == order_id)
        count_query = count_query.where(WorkPermit.order_id == order_id)
    if status is not None:
        query = query.where(WorkPermit.status == status.value)
        count_query = count_query.where(WorkPermit.status == status.value)
    total = session.scalar(count_query) or 0
    permits = session.scalars(
        query.order_by(WorkPermit.created_at, WorkPermit.id).offset(page.offset).limit(page.limit)
    ).all()
    return Page[WorkPermitResponse](
        items=[work_permit_response(permit) for permit in permits],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{permit_id}", response_model=WorkPermitResponse)
def get_permit_endpoint(permit_id: str, session: SessionDep) -> WorkPermitResponse:
    return work_permit_response(get_permit(session, permit_id))


@router.post("/{permit_id}/issue", response_model=WorkPermitResponse)
def issue_permit(
    permit_id: str,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> WorkPermitResponse:
    permit = get_permit(session, permit_id)
    access.require_site(site_of_order_id(session, permit.order_id))
    return work_permit_response(transition_permit(session, actor, permit, WorkPermitStatus.ISSUED))


@router.post("/{permit_id}/close", response_model=WorkPermitResponse)
def close_permit(
    permit_id: str,
    payload: WorkPermitClose,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> WorkPermitResponse:
    permit = get_permit(session, permit_id)
    access.require_site(site_of_order_id(session, permit.order_id))
    return work_permit_response(
        transition_permit(
            session,
            actor,
            permit,
            WorkPermitStatus.CLOSED,
            note=payload.completion_note,
        )
    )


@router.post("/{permit_id}/revoke", response_model=WorkPermitResponse)
def revoke_permit(
    permit_id: str,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
    payload: WorkPermitRevoke | None = None,
) -> WorkPermitResponse:
    permit = get_permit(session, permit_id)
    access.require_site(site_of_order_id(session, permit.order_id))
    reason = payload.reason if payload is not None else None
    return work_permit_response(
        transition_permit(
            session,
            actor,
            permit,
            WorkPermitStatus.REVOKED,
            note=reason,
        )
    )
