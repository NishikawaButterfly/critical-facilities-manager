"""Incident endpoints. Transitions are explicit POST actions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..authz import site_of_asset_id
from ..domain import IncidentSeverity, IncidentStatus
from ..models import Incident
from ..schemas import (
    IncidentCorrectiveOrder,
    IncidentCreate,
    IncidentDismiss,
    IncidentResolve,
    IncidentResponse,
    MaintenanceOrderResponse,
    Page,
)
from ..services.incidents import (
    create_corrective_order,
    create_incident,
    get_incident,
    read_incident,
    transition_incident,
)
from .deps import ActorDep, PageDep, SessionDep, SiteAccessDep
from .serializers import incident_response, order_response

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post("", response_model=IncidentResponse, status_code=201)
def create_incident_endpoint(
    payload: IncidentCreate,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> IncidentResponse:
    access.require_site(site_of_asset_id(session, payload.asset_id))
    incident = create_incident(
        session,
        actor,
        asset_id=payload.asset_id,
        severity=payload.severity,
        title=payload.title,
        description=payload.description,
    )
    return incident_response(incident)


@router.get("", response_model=Page[IncidentResponse])
def list_incidents(
    session: SessionDep,
    page: PageDep,
    access: SiteAccessDep,
    *,
    asset_id: Annotated[str | None, Query()] = None,
    severity: Annotated[IncidentSeverity | None, Query()] = None,
    status: Annotated[IncidentStatus | None, Query()] = None,
) -> Page[IncidentResponse]:
    readable = access.readable(Incident)
    query = select(Incident).where(readable)
    count_query = select(func.count()).select_from(Incident).where(readable)
    if asset_id is not None:
        query = query.where(Incident.asset_id == asset_id)
        count_query = count_query.where(Incident.asset_id == asset_id)
    if severity is not None:
        query = query.where(Incident.severity == severity.value)
        count_query = count_query.where(Incident.severity == severity.value)
    if status is not None:
        query = query.where(Incident.status == status.value)
        count_query = count_query.where(Incident.status == status.value)
    total = session.scalar(count_query) or 0
    incidents = session.scalars(
        query.order_by(Incident.created_at, Incident.id).offset(page.offset).limit(page.limit)
    ).all()
    return Page[IncidentResponse](
        items=[incident_response(incident) for incident in incidents],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{incident_id}", response_model=IncidentResponse)
def get_incident_endpoint(
    incident_id: str, session: SessionDep, access: SiteAccessDep
) -> IncidentResponse:
    return incident_response(read_incident(session, incident_id, access.readable(Incident)))


@router.post("/{incident_id}/start-investigation", response_model=IncidentResponse)
def start_investigation(
    incident_id: str,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> IncidentResponse:
    incident = get_incident(session, incident_id)
    access.require_site(site_of_asset_id(session, incident.asset_id))
    return incident_response(
        transition_incident(session, actor, incident, IncidentStatus.INVESTIGATING)
    )


@router.post("/{incident_id}/resolve", response_model=IncidentResponse)
def resolve_incident(
    incident_id: str,
    payload: IncidentResolve,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> IncidentResponse:
    incident = get_incident(session, incident_id)
    access.require_site(site_of_asset_id(session, incident.asset_id))
    return incident_response(
        transition_incident(
            session,
            actor,
            incident,
            IncidentStatus.RESOLVED,
            notes=payload.resolution_notes,
        )
    )


@router.post("/{incident_id}/dismiss", response_model=IncidentResponse)
def dismiss_incident(
    incident_id: str,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
    payload: IncidentDismiss | None = None,
) -> IncidentResponse:
    incident = get_incident(session, incident_id)
    access.require_site(site_of_asset_id(session, incident.asset_id))
    reason = payload.reason if payload is not None else None
    return incident_response(
        transition_incident(
            session,
            actor,
            incident,
            IncidentStatus.DISMISSED,
            notes=reason,
        )
    )


@router.post(
    "/{incident_id}/corrective-order",
    response_model=MaintenanceOrderResponse,
    status_code=201,
)
def create_corrective_order_endpoint(
    incident_id: str,
    payload: IncidentCorrectiveOrder,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> MaintenanceOrderResponse:
    incident = get_incident(session, incident_id)
    access.require_site(site_of_asset_id(session, incident.asset_id))
    order = create_corrective_order(
        session,
        actor,
        incident,
        due_date=payload.due_date,
        title=payload.title,
        description=payload.description,
    )
    return order_response(order)
