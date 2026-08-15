"""Read-only access to the append-only audit trail."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..models import AuditEntry
from ..schemas import AuditEntryResponse, Page
from .deps import PageDep, SessionDep, SiteAccessDep
from .serializers import audit_entry_response

router = APIRouter(prefix="/audit-entries", tags=["audit"])


@router.get("", response_model=Page[AuditEntryResponse])
def list_audit_entries(
    session: SessionDep,
    page: PageDep,
    access: SiteAccessDep,
    *,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
) -> Page[AuditEntryResponse]:
    readable = access.readable(AuditEntry)
    query = select(AuditEntry).where(readable)
    count_query = select(func.count()).select_from(AuditEntry).where(readable)
    if entity_type is not None:
        query = query.where(AuditEntry.entity_type == entity_type)
        count_query = count_query.where(AuditEntry.entity_type == entity_type)
    if entity_id is not None:
        query = query.where(AuditEntry.entity_id == entity_id)
        count_query = count_query.where(AuditEntry.entity_id == entity_id)
    if actor is not None:
        query = query.where(AuditEntry.actor == actor)
        count_query = count_query.where(AuditEntry.actor == actor)
    total = session.scalar(count_query) or 0
    entries = session.scalars(
        query.order_by(AuditEntry.id.desc()).offset(page.offset).limit(page.limit)
    ).all()
    return Page[AuditEntryResponse](
        items=[audit_entry_response(entry) for entry in entries],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
