"""Punch item endpoints. Transitions are explicit POST actions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import ColumnElement, and_, func, select

from ..authz import site_of_asset_id, site_of_location_id, site_of_punch_item
from ..domain import PunchItemCategory, PunchItemSeverity, PunchItemStatus
from ..models import PunchItem
from ..schemas import Page, PunchItemClose, PunchItemCreate, PunchItemResponse
from ..services.punch_items import create_punch_item, get_punch_item, transition_punch_item
from .deps import ActorDep, PageDep, SessionDep, SiteAccessDep
from .serializers import punch_item_response

router = APIRouter(prefix="/punch-items", tags=["punch-items"])


def _overdue_clause(today: date) -> ColumnElement[bool]:
    return and_(
        PunchItem.due_date.is_not(None),
        PunchItem.due_date < today,
        PunchItem.status != PunchItemStatus.CLOSED.value,
    )


@router.post("", response_model=PunchItemResponse, status_code=201)
def create_punch_item_endpoint(
    payload: PunchItemCreate,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> PunchItemResponse:
    if payload.asset_id is not None:
        access.require_site(site_of_asset_id(session, payload.asset_id))
    elif payload.location_id is not None:
        access.require_site(site_of_location_id(session, payload.location_id))
    item = create_punch_item(
        session,
        actor,
        asset_id=payload.asset_id,
        location_id=payload.location_id,
        category=payload.category,
        severity=payload.severity,
        title=payload.title,
        description=payload.description,
        due_date=payload.due_date,
    )
    return punch_item_response(item)


@router.get("", response_model=Page[PunchItemResponse])
def list_punch_items(
    session: SessionDep,
    page: PageDep,
    *,
    asset_id: Annotated[str | None, Query()] = None,
    location_id: Annotated[str | None, Query()] = None,
    category: Annotated[PunchItemCategory | None, Query()] = None,
    severity: Annotated[PunchItemSeverity | None, Query()] = None,
    status: Annotated[PunchItemStatus | None, Query()] = None,
    overdue: Annotated[bool | None, Query()] = None,
) -> Page[PunchItemResponse]:
    query = select(PunchItem)
    count_query = select(func.count()).select_from(PunchItem)
    if asset_id is not None:
        query = query.where(PunchItem.asset_id == asset_id)
        count_query = count_query.where(PunchItem.asset_id == asset_id)
    if location_id is not None:
        query = query.where(PunchItem.location_id == location_id)
        count_query = count_query.where(PunchItem.location_id == location_id)
    if category is not None:
        query = query.where(PunchItem.category == category.value)
        count_query = count_query.where(PunchItem.category == category.value)
    if severity is not None:
        query = query.where(PunchItem.severity == severity.value)
        count_query = count_query.where(PunchItem.severity == severity.value)
    if status is not None:
        query = query.where(PunchItem.status == status.value)
        count_query = count_query.where(PunchItem.status == status.value)
    if overdue is not None:
        clause = _overdue_clause(datetime.now(UTC).date())
        if not overdue:
            clause = ~clause
        query = query.where(clause)
        count_query = count_query.where(clause)
    total = session.scalar(count_query) or 0
    items = session.scalars(
        query.order_by(PunchItem.created_at, PunchItem.id).offset(page.offset).limit(page.limit)
    ).all()
    return Page[PunchItemResponse](
        items=[punch_item_response(item) for item in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{punch_item_id}", response_model=PunchItemResponse)
def get_punch_item_endpoint(punch_item_id: str, session: SessionDep) -> PunchItemResponse:
    return punch_item_response(get_punch_item(session, punch_item_id))


@router.post("/{punch_item_id}/start", response_model=PunchItemResponse)
def start_punch_item(
    punch_item_id: str,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> PunchItemResponse:
    item = get_punch_item(session, punch_item_id)
    access.require_site(site_of_punch_item(session, item))
    return punch_item_response(
        transition_punch_item(session, actor, item, PunchItemStatus.IN_PROGRESS)
    )


@router.post("/{punch_item_id}/close", response_model=PunchItemResponse)
def close_punch_item(
    punch_item_id: str,
    payload: PunchItemClose,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> PunchItemResponse:
    item = get_punch_item(session, punch_item_id)
    access.require_site(site_of_punch_item(session, item))
    return punch_item_response(
        transition_punch_item(
            session,
            actor,
            item,
            PunchItemStatus.CLOSED,
            closing_note=payload.closing_note,
        )
    )
