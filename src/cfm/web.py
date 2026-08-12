"""Static web frontend served by the API application.

The frontend is plain HTML, CSS, and ES modules with no build step and no
runtime dependency of its own; the files ship inside the package and this
module is the only wiring they need.

Two details are load-bearing:

*Media types.* Python's :mod:`mimetypes` seeds itself from the Windows
registry, where ``.js`` is commonly registered as ``text/plain``. A module
script served as ``text/plain`` is refused outright by every browser -- the
strict check for module scripts has no sniffing escape hatch -- so the
frontend would simply not run on such a machine. The types the frontend
actually serves are therefore pinned here, before any response is built, and
:mod:`tests.test_web` asserts the headers that come out.

*No sniffing.* Static responses carry ``X-Content-Type-Options: nosniff``, so
a browser never second-guesses the declared type. That makes a wrong media
type fail loudly rather than work by accident on some browsers and not
others -- which is the behaviour the pin above is tested against.

The static files are mounted under their own prefix rather than at the root:
mounting at ``/`` would swallow unmatched API paths and turn the API's
problem-details 404 and 405 responses into the static handler's, and those
responses are part of the published contract.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import RedirectResponse
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from .config import Settings

WEB_DIR = Path(__file__).parent / "web"
"""Directory holding the frontend, shipped as package data."""

WEB_PREFIX = "/ui"
"""Mount point of the frontend, kept clear of the API prefix."""

WEB_INDEX = f"{WEB_PREFIX}/"
"""Canonical address of the page; ``/`` redirects here."""

PINNED_MEDIA_TYPES: dict[str, str] = {
    ".css": "text/css",
    ".html": "text/html",
    ".js": "text/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
}
"""Media type for every extension the frontend serves, pinned by suffix."""


def pin_media_types() -> None:
    """Override any platform mapping for the extensions the frontend serves.

    Called before the mount is created, so the table is already correct by
    the time :class:`WebStaticFiles` guesses a type.
    """
    for suffix, media_type in PINNED_MEDIA_TYPES.items():
        mimetypes.add_type(media_type, suffix)


class WebStaticFiles(StaticFiles):
    """Static files that refuse to be sniffed."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def build_web_router(settings: Settings) -> APIRouter:
    """Routes that belong to the page rather than to the API.

    Kept out of the OpenAPI document: they serve a browser, not a client of
    the versioned API.
    """
    router = APIRouter(include_in_schema=False)

    @router.get("/")
    def web_root() -> RedirectResponse:
        return RedirectResponse(WEB_INDEX)

    @router.get(f"{WEB_PREFIX}/config.json")
    def web_config() -> dict[str, str]:
        """What the page needs before it can make its first API call.

        The API prefix is configurable, and a static page cannot read the
        server's settings; without this it would have to hardcode a guess.
        """
        return {
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "api_prefix": settings.api_prefix,
        }

    return router


def mount_web(app: FastAPI, settings: Settings) -> None:
    """Attach the frontend to ``app``.

    The router is included before the mount so ``/ui/config.json`` resolves
    to the route rather than to a file that does not exist on disk.
    """
    pin_media_types()
    app.include_router(build_web_router(settings))
    app.mount(WEB_PREFIX, WebStaticFiles(directory=WEB_DIR, html=True), name="web")
