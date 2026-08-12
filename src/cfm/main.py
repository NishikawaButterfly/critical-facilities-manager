"""Application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .bodylimit import EvidenceBodyLimitMiddleware
from .config import Settings, get_settings
from .database import build_engine, build_session_factory
from .evidence import EvidenceStore
from .migrate import ensure_schema_current, upgrade_to_head
from .problems import register_problem_handlers
from .ratelimit import AuthRateLimiter
from .web import mount_web


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    engine = build_engine(runtime_settings.database_url)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # The schema belongs to the migration chain, not to startup: by
        # default the application refuses to serve a database that is not
        # at the head revision and tells the operator how to migrate.
        # CFM_DB_AUTO_UPGRADE=true opts into running the migrations here
        # instead, for single-process deployments.
        if runtime_settings.db_auto_upgrade:
            upgrade_to_head(runtime_settings.database_url)
        else:
            ensure_schema_current(runtime_settings.database_url)
        yield
        engine.dispose()

    docs_url = "/docs" if runtime_settings.docs_enabled else None
    redoc_url = "/redoc" if runtime_settings.docs_enabled else None
    openapi_url = "/openapi.json" if runtime_settings.docs_enabled else None
    application = FastAPI(
        title=runtime_settings.app_name,
        version=runtime_settings.app_version,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.engine = engine
    application.state.session_factory = session_factory
    # Per-process by design: exact for the single-process story, and with
    # several workers each process keeps its own counters (documented in
    # the README's rate-limiting section).
    application.state.auth_limiter = AuthRateLimiter(
        max_failures=runtime_settings.auth_max_failures,
        window_seconds=runtime_settings.auth_failure_window_seconds,
        lockout_seconds=runtime_settings.auth_lockout_seconds,
    )
    application.state.evidence_store = EvidenceStore(runtime_settings.evidence_dir)
    # Added before CORS so CORS stays outermost and even a 413 rejected
    # here reaches browsers with the usual CORS headers.
    application.add_middleware(
        EvidenceBodyLimitMiddleware,
        api_prefix=runtime_settings.api_prefix,
        max_file_bytes=runtime_settings.evidence_max_mb * 1024 * 1024,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_problem_handlers(application)
    application.include_router(router, prefix=runtime_settings.api_prefix)
    if runtime_settings.web_enabled:
        # Added last, so every API route is matched before the frontend's
        # mount is reached and the API's own 404 and 405 responses stand.
        mount_web(application, runtime_settings)
    return application


app = create_app()
