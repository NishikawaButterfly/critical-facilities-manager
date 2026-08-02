"""Procedure endpoints. Lifecycle changes are explicit POST actions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..domain import ProcedureKind, ProcedureStatus
from ..models import Procedure
from ..schemas import Page, ProcedureCreate, ProcedureResponse, ProcedureUpdate
from ..services.procedures import (
    approve_procedure,
    create_new_version,
    create_procedure,
    get_procedure,
    get_version_chain,
    retire_procedure,
    update_procedure,
)
from .deps import ActorDep, PageDep, SessionDep
from .serializers import procedure_response

router = APIRouter(prefix="/procedures", tags=["procedures"])


@router.post("", response_model=ProcedureResponse, status_code=201)
def create_procedure_endpoint(
    payload: ProcedureCreate,
    session: SessionDep,
    actor: ActorDep,
) -> ProcedureResponse:
    procedure = create_procedure(
        session,
        actor,
        kind=payload.kind,
        title=payload.title,
        steps=payload.steps,
    )
    return procedure_response(procedure)


@router.get("", response_model=Page[ProcedureResponse])
def list_procedures(
    session: SessionDep,
    page: PageDep,
    kind: Annotated[ProcedureKind | None, Query()] = None,
    status: Annotated[ProcedureStatus | None, Query()] = None,
) -> Page[ProcedureResponse]:
    query = select(Procedure)
    count_query = select(func.count()).select_from(Procedure)
    if kind is not None:
        query = query.where(Procedure.kind == kind.value)
        count_query = count_query.where(Procedure.kind == kind.value)
    if status is not None:
        query = query.where(Procedure.status == status.value)
        count_query = count_query.where(Procedure.status == status.value)
    total = session.scalar(count_query) or 0
    procedures = session.scalars(
        query.order_by(Procedure.created_at, Procedure.id).offset(page.offset).limit(page.limit)
    ).all()
    return Page[ProcedureResponse](
        items=[procedure_response(procedure) for procedure in procedures],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{procedure_id}", response_model=ProcedureResponse)
def get_procedure_endpoint(procedure_id: str, session: SessionDep) -> ProcedureResponse:
    return procedure_response(get_procedure(session, procedure_id))


@router.get("/{procedure_id}/versions", response_model=list[ProcedureResponse])
def get_version_chain_endpoint(procedure_id: str, session: SessionDep) -> list[ProcedureResponse]:
    procedure = get_procedure(session, procedure_id)
    return [procedure_response(version) for version in get_version_chain(session, procedure)]


@router.patch("/{procedure_id}", response_model=ProcedureResponse)
def update_procedure_endpoint(
    procedure_id: str,
    payload: ProcedureUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> ProcedureResponse:
    procedure = get_procedure(session, procedure_id)
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    return procedure_response(update_procedure(session, actor, procedure, updates))


@router.post("/{procedure_id}/approve", response_model=ProcedureResponse)
def approve_procedure_endpoint(
    procedure_id: str,
    session: SessionDep,
    actor: ActorDep,
) -> ProcedureResponse:
    procedure = get_procedure(session, procedure_id)
    return procedure_response(approve_procedure(session, actor, procedure))


@router.post("/{procedure_id}/retire", response_model=ProcedureResponse)
def retire_procedure_endpoint(
    procedure_id: str,
    session: SessionDep,
    actor: ActorDep,
) -> ProcedureResponse:
    procedure = get_procedure(session, procedure_id)
    return procedure_response(retire_procedure(session, actor, procedure))


@router.post("/{procedure_id}/new-version", response_model=ProcedureResponse, status_code=201)
def create_new_version_endpoint(
    procedure_id: str,
    session: SessionDep,
    actor: ActorDep,
) -> ProcedureResponse:
    procedure = get_procedure(session, procedure_id)
    return procedure_response(create_new_version(session, actor, procedure))
