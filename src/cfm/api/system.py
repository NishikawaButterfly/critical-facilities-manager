"""System endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from ..config import Settings
from ..schemas import HealthResponse
from .deps import SessionDep

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request, session: SessionDep) -> HealthResponse:
    session.execute(text("SELECT 1"))
    settings: Settings = request.app.state.settings
    return HealthResponse(status="ok", version=settings.app_version, database="ok")
