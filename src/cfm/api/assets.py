"""Asset inventory endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..domain import AssetCriticality, AssetStatus
from ..models import Asset
from ..schemas import AssetCreate, AssetResponse, AssetTransition, AssetUpdate, Page
from ..services.assets import create_asset, get_asset, transition_asset, update_asset
from .deps import ActorDep, PageDep, SessionDep
from .serializers import asset_response

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post("", response_model=AssetResponse, status_code=201)
def create_asset_endpoint(
    payload: AssetCreate,
    session: SessionDep,
    actor: ActorDep,
) -> AssetResponse:
    asset = create_asset(
        session,
        actor,
        tag=payload.tag,
        name=payload.name,
        asset_type=payload.asset_type,
        criticality=payload.criticality,
        location_id=payload.location_id,
        status=payload.status,
    )
    return asset_response(asset)


@router.get("", response_model=Page[AssetResponse])
def list_assets(
    session: SessionDep,
    page: PageDep,
    *,
    location_id: Annotated[str | None, Query()] = None,
    site_id: Annotated[str | None, Query()] = None,
    status: Annotated[AssetStatus | None, Query()] = None,
    criticality: Annotated[AssetCriticality | None, Query()] = None,
) -> Page[AssetResponse]:
    query = select(Asset)
    count_query = select(func.count()).select_from(Asset)
    if location_id is not None:
        query = query.where(Asset.location_id == location_id)
        count_query = count_query.where(Asset.location_id == location_id)
    if site_id is not None:
        query = query.where(Asset.site_id == site_id)
        count_query = count_query.where(Asset.site_id == site_id)
    if status is not None:
        query = query.where(Asset.status == status.value)
        count_query = count_query.where(Asset.status == status.value)
    if criticality is not None:
        query = query.where(Asset.criticality == criticality.value)
        count_query = count_query.where(Asset.criticality == criticality.value)
    total = session.scalar(count_query) or 0
    assets = session.scalars(
        query.order_by(Asset.created_at, Asset.id).offset(page.offset).limit(page.limit)
    ).all()
    return Page[AssetResponse](
        items=[asset_response(asset) for asset in assets],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset_endpoint(asset_id: str, session: SessionDep) -> AssetResponse:
    return asset_response(get_asset(session, asset_id))


@router.patch("/{asset_id}", response_model=AssetResponse)
def update_asset_endpoint(
    asset_id: str,
    payload: AssetUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> AssetResponse:
    asset = get_asset(session, asset_id)
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    return asset_response(update_asset(session, actor, asset, updates))


@router.post("/{asset_id}/transition", response_model=AssetResponse)
def transition_asset_endpoint(
    asset_id: str,
    payload: AssetTransition,
    session: SessionDep,
    actor: ActorDep,
) -> AssetResponse:
    asset = get_asset(session, asset_id)
    return asset_response(transition_asset(session, actor, asset, payload.to_status))
