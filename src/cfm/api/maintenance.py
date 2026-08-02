"""Maintenance order endpoints. Transitions are explicit POST actions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..domain import MaintenanceOrderStatus, MaintenanceOrderType
from ..models import MaintenanceOrder
from ..schemas import (
    MaintenanceOrderCancel,
    MaintenanceOrderComplete,
    MaintenanceOrderCreate,
    MaintenanceOrderResponse,
    MaintenanceOrderUpdate,
    Page,
)
from ..services.maintenance import (
    create_order,
    get_order,
    transition_order,
    update_order,
)
from .deps import ActorDep, PageDep, SessionDep
from .serializers import order_response

router = APIRouter(prefix="/maintenance-orders", tags=["maintenance-orders"])


@router.post("", response_model=MaintenanceOrderResponse, status_code=201)
def create_order_endpoint(
    payload: MaintenanceOrderCreate,
    session: SessionDep,
    actor: ActorDep,
) -> MaintenanceOrderResponse:
    order = create_order(
        session,
        actor,
        order_type=payload.order_type,
        asset_id=payload.asset_id,
        title=payload.title,
        description=payload.description,
        due_date=payload.due_date,
    )
    return order_response(order)


@router.get("", response_model=Page[MaintenanceOrderResponse])
def list_orders(
    session: SessionDep,
    page: PageDep,
    asset_id: Annotated[str | None, Query()] = None,
    status: Annotated[MaintenanceOrderStatus | None, Query()] = None,
    order_type: Annotated[MaintenanceOrderType | None, Query()] = None,
) -> Page[MaintenanceOrderResponse]:
    query = select(MaintenanceOrder)
    count_query = select(func.count()).select_from(MaintenanceOrder)
    if asset_id is not None:
        query = query.where(MaintenanceOrder.asset_id == asset_id)
        count_query = count_query.where(MaintenanceOrder.asset_id == asset_id)
    if status is not None:
        query = query.where(MaintenanceOrder.status == status.value)
        count_query = count_query.where(MaintenanceOrder.status == status.value)
    if order_type is not None:
        query = query.where(MaintenanceOrder.order_type == order_type.value)
        count_query = count_query.where(MaintenanceOrder.order_type == order_type.value)
    total = session.scalar(count_query) or 0
    orders = session.scalars(
        query.order_by(MaintenanceOrder.created_at, MaintenanceOrder.id)
        .offset(page.offset)
        .limit(page.limit)
    ).all()
    return Page[MaintenanceOrderResponse](
        items=[order_response(order) for order in orders],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{order_id}", response_model=MaintenanceOrderResponse)
def get_order_endpoint(order_id: str, session: SessionDep) -> MaintenanceOrderResponse:
    return order_response(get_order(session, order_id))


@router.patch("/{order_id}", response_model=MaintenanceOrderResponse)
def update_order_endpoint(
    order_id: str,
    payload: MaintenanceOrderUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> MaintenanceOrderResponse:
    order = get_order(session, order_id)
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    return order_response(update_order(session, actor, order, updates))


@router.post("/{order_id}/schedule", response_model=MaintenanceOrderResponse)
def schedule_order(order_id: str, session: SessionDep, actor: ActorDep) -> MaintenanceOrderResponse:
    order = get_order(session, order_id)
    return order_response(transition_order(session, actor, order, MaintenanceOrderStatus.SCHEDULED))


@router.post("/{order_id}/start", response_model=MaintenanceOrderResponse)
def start_order(order_id: str, session: SessionDep, actor: ActorDep) -> MaintenanceOrderResponse:
    order = get_order(session, order_id)
    return order_response(
        transition_order(session, actor, order, MaintenanceOrderStatus.IN_PROGRESS)
    )


@router.post("/{order_id}/complete", response_model=MaintenanceOrderResponse)
def complete_order(
    order_id: str,
    payload: MaintenanceOrderComplete,
    session: SessionDep,
    actor: ActorDep,
) -> MaintenanceOrderResponse:
    order = get_order(session, order_id)
    return order_response(
        transition_order(
            session,
            actor,
            order,
            MaintenanceOrderStatus.DONE,
            notes=payload.completion_notes,
        )
    )


@router.post("/{order_id}/cancel", response_model=MaintenanceOrderResponse)
def cancel_order(
    order_id: str,
    session: SessionDep,
    actor: ActorDep,
    payload: MaintenanceOrderCancel | None = None,
) -> MaintenanceOrderResponse:
    order = get_order(session, order_id)
    reason = payload.reason if payload is not None else None
    return order_response(
        transition_order(
            session,
            actor,
            order,
            MaintenanceOrderStatus.CANCELLED,
            notes=reason,
        )
    )
