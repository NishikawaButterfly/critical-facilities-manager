"""Location hierarchy endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..authz import site_of_location, site_of_location_id
from ..domain import LocationKind
from ..models import Location
from ..schemas import LocationCreate, LocationResponse, LocationUpdate, Page
from ..services.locations import (
    create_location,
    delete_location,
    get_location,
    read_location,
    update_location,
)
from .deps import ActorDep, PageDep, SessionDep, SiteAccessDep
from .serializers import location_response

router = APIRouter(prefix="/locations", tags=["locations"])


@router.post("", response_model=LocationResponse, status_code=201)
def create_location_endpoint(
    payload: LocationCreate,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> LocationResponse:
    if payload.kind is LocationKind.SITE:
        # Creating a site changes the set of scopable sites.
        access.require_installation()
    elif payload.parent_id is not None:
        access.require_site(site_of_location_id(session, payload.parent_id))
    location = create_location(
        session,
        actor,
        kind=payload.kind,
        code=payload.code,
        name=payload.name,
        parent_id=payload.parent_id,
    )
    return location_response(location)


@router.get("", response_model=Page[LocationResponse])
def list_locations(
    session: SessionDep,
    page: PageDep,
    access: SiteAccessDep,
    kind: Annotated[LocationKind | None, Query()] = None,
    parent_id: Annotated[str | None, Query()] = None,
) -> Page[LocationResponse]:
    readable = access.readable(Location)
    query = select(Location).where(readable)
    count_query = select(func.count()).select_from(Location).where(readable)
    if kind is not None:
        query = query.where(Location.kind == kind.value)
        count_query = count_query.where(Location.kind == kind.value)
    if parent_id is not None:
        query = query.where(Location.parent_id == parent_id)
        count_query = count_query.where(Location.parent_id == parent_id)
    total = session.scalar(count_query) or 0
    locations = session.scalars(
        query.order_by(Location.created_at, Location.id).offset(page.offset).limit(page.limit)
    ).all()
    return Page[LocationResponse](
        items=[location_response(location) for location in locations],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{location_id}", response_model=LocationResponse)
def get_location_endpoint(
    location_id: str, session: SessionDep, access: SiteAccessDep
) -> LocationResponse:
    return location_response(read_location(session, location_id, access.readable(Location)))


@router.patch("/{location_id}", response_model=LocationResponse)
def update_location_endpoint(
    location_id: str,
    payload: LocationUpdate,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> LocationResponse:
    location = get_location(session, location_id)
    access.require_site(site_of_location(session, location))
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    return location_response(update_location(session, actor, location, updates))


@router.delete("/{location_id}", status_code=204)
def delete_location_endpoint(
    location_id: str,
    session: SessionDep,
    actor: ActorDep,
    access: SiteAccessDep,
) -> None:
    location = get_location(session, location_id)
    if location.kind == LocationKind.SITE.value:
        # Removing a site changes the set of scopable sites.
        access.require_installation()
    else:
        access.require_site(site_of_location(session, location))
    delete_location(session, actor, location)
