"""Operational constraint endpoints. Constraints are created and retired, never edited."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..domain import ConstraintKind, ConstraintStatus
from ..models import Constraint, ConstraintMember
from ..schemas import ConstraintCreate, ConstraintResponse, Page
from ..services.constraints import create_constraint, get_constraint, retire_constraint
from .deps import ActorDep, PageDep, SessionDep, SiteAccessDep
from .serializers import constraint_response

router = APIRouter(prefix="/constraints", tags=["constraints"])


@router.post("", response_model=ConstraintResponse, status_code=201)
def create_constraint_endpoint(
    payload: ConstraintCreate,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> ConstraintResponse:
    access.require_installation()
    constraint = create_constraint(
        session,
        actor,
        kind=payload.kind,
        name=payload.name,
        description=payload.description,
        asset_ids=payload.asset_ids,
    )
    return constraint_response(constraint)


@router.get("", response_model=Page[ConstraintResponse])
def list_constraints(
    session: SessionDep,
    page: PageDep,
    kind: Annotated[ConstraintKind | None, Query()] = None,
    status: Annotated[ConstraintStatus | None, Query()] = None,
    asset_id: Annotated[str | None, Query()] = None,
) -> Page[ConstraintResponse]:
    query = select(Constraint)
    count_query = select(func.count()).select_from(Constraint)
    if kind is not None:
        query = query.where(Constraint.kind == kind.value)
        count_query = count_query.where(Constraint.kind == kind.value)
    if status is not None:
        query = query.where(Constraint.status == status.value)
        count_query = count_query.where(Constraint.status == status.value)
    if asset_id is not None:
        member_join = ConstraintMember.constraint_id == Constraint.id
        query = query.join(ConstraintMember, member_join).where(
            ConstraintMember.asset_id == asset_id
        )
        count_query = count_query.join(ConstraintMember, member_join).where(
            ConstraintMember.asset_id == asset_id
        )
    total = session.scalar(count_query) or 0
    constraints = session.scalars(
        query.order_by(Constraint.created_at, Constraint.id).offset(page.offset).limit(page.limit)
    ).all()
    return Page[ConstraintResponse](
        items=[constraint_response(constraint) for constraint in constraints],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{constraint_id}", response_model=ConstraintResponse)
def get_constraint_endpoint(constraint_id: str, session: SessionDep) -> ConstraintResponse:
    return constraint_response(get_constraint(session, constraint_id))


@router.post("/{constraint_id}/retire", response_model=ConstraintResponse)
def retire_constraint_endpoint(
    constraint_id: str,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> ConstraintResponse:
    access.require_installation()
    constraint = get_constraint(session, constraint_id)
    return constraint_response(retire_constraint(session, actor, constraint))
