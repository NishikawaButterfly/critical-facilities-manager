"""Operational constraint endpoints. Constraints are created and retired, never edited.

A constraint may bind assets on different sites, so it belongs to none of
them: writing one takes an installation-wide grant, and reading one is open
to any authenticated user, because the engineer whose order was refused by
a constraint has to be able to read what refused it. Its membership is
another matter — those are assets, and assets belong to sites — so the
member list a caller sees carries only the assets their grants cover, and
the ``asset_id`` filter matches only those too.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain import ConstraintKind, ConstraintStatus
from ..models import Constraint, ConstraintMember
from ..schemas import ConstraintCreate, ConstraintResponse, Page
from ..services.constraints import create_constraint, get_constraint, retire_constraint
from .deps import ActorDep, PageDep, SessionDep, SiteAccessDep
from .serializers import constraint_response

router = APIRouter(prefix="/constraints", tags=["constraints"])


def _readable_members(
    session: Session,
    access: SiteAccessDep,
    constraints: Sequence[Constraint],
) -> dict[str, list[str]]:
    """The member assets of each constraint that the caller may read.

    One query for the whole page, filtered in SQL like every other read.
    """
    if not constraints:
        return {}
    rows = session.execute(
        select(ConstraintMember.constraint_id, ConstraintMember.asset_id)
        .where(
            ConstraintMember.constraint_id.in_([constraint.id for constraint in constraints]),
            access.readable(ConstraintMember),
        )
        .order_by(ConstraintMember.id)
    ).all()
    members: dict[str, list[str]] = {constraint.id: [] for constraint in constraints}
    for constraint_id, asset_id in rows:
        members[constraint_id].append(asset_id)
    return members


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
    return constraint_response(constraint, payload.asset_ids)


@router.get("", response_model=Page[ConstraintResponse])
def list_constraints(
    session: SessionDep,
    page: PageDep,
    access: SiteAccessDep,
    *,
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
        # Membership is site-owned, so an asset the caller may not read
        # matches nothing — exactly as an asset that does not exist.
        member_join = ConstraintMember.constraint_id == Constraint.id
        member_filter = (
            ConstraintMember.asset_id == asset_id,
            access.readable(ConstraintMember),
        )
        query = query.join(ConstraintMember, member_join).where(*member_filter)
        count_query = count_query.join(ConstraintMember, member_join).where(*member_filter)
    total = session.scalar(count_query) or 0
    constraints = session.scalars(
        query.order_by(Constraint.created_at, Constraint.id).offset(page.offset).limit(page.limit)
    ).all()
    members = _readable_members(session, access, constraints)
    return Page[ConstraintResponse](
        items=[
            constraint_response(constraint, members[constraint.id]) for constraint in constraints
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{constraint_id}", response_model=ConstraintResponse)
def get_constraint_endpoint(
    constraint_id: str, session: SessionDep, access: SiteAccessDep
) -> ConstraintResponse:
    constraint = get_constraint(session, constraint_id)
    members = _readable_members(session, access, [constraint])
    return constraint_response(constraint, members[constraint.id])


@router.post("/{constraint_id}/retire", response_model=ConstraintResponse)
def retire_constraint_endpoint(
    constraint_id: str,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> ConstraintResponse:
    access.require_installation()
    constraint = get_constraint(session, constraint_id)
    retired = retire_constraint(session, actor, constraint)
    return constraint_response(retired, _readable_members(session, access, [retired])[retired.id])
