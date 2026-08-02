"""Application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import Settings, get_settings
from .database import Base, build_engine, build_session_factory
from .problems import register_problem_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    engine = build_engine(runtime_settings.database_url)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        Base.metadata.create_all(engine)
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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_problem_handlers(application)
    application.include_router(router, prefix=runtime_settings.api_prefix)
    return application


app = create_app()
