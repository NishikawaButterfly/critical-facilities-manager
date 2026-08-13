"""The bundled web frontend: how it is served, and what it assumes of the API.

The frontend has no test runner of its own -- it is plain ES modules with no
build step -- so the checks that keep it honest live here, in the suite that
already runs everywhere. They fall into three groups: the files reach the
browser correctly labelled, the page's assumptions about the API are pinned to
the API's own definitions, and the shipped JavaScript stays inside the rules it
claims to follow.
"""

from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import tomllib
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from cfm import __version__
from cfm.config import Settings
from cfm.domain import ORDER_TRANSITIONS, MaintenanceOrderStatus
from cfm.main import create_app
from cfm.web import PINNED_MEDIA_TYPES, WEB_DIR, WEB_INDEX, pin_media_types

from .conftest import build_app

pytestmark = pytest.mark.anyio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ORDER_ACTIONS = json.loads((WEB_DIR / "order-actions.json").read_text(encoding="utf-8"))
SHIPPED_FILES = sorted(path for path in WEB_DIR.rglob("*") if path.is_file())
SHIPPED_SCRIPTS = [path for path in SHIPPED_FILES if path.suffix == ".js"]


def inspection_app(tmp_path: Path) -> FastAPI:
    """An application built only to read its routes and schema back.

    Nothing here opens the database or the evidence directory, so the paths
    only have to be somewhere harmless.
    """
    return create_app(
        Settings(
            database_url=f"sqlite:///{(tmp_path / 'routes.db').as_posix()}",
            evidence_dir=tmp_path / "evidence",
        )
    )


# --------------------------------------------------------------------------
# Serving
# --------------------------------------------------------------------------


async def test_page_is_served_at_its_own_prefix(anon_client: AsyncClient) -> None:
    response = await anon_client.get(WEB_INDEX)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "Critical Facilities Manager" in body
    # The three views the issue asks for, and nothing pretending to be a login.
    assert "Asset tree" in body
    assert "Order board" in body
    assert "Audit timeline" in body


async def test_root_redirects_to_the_page(anon_client: AsyncClient) -> None:
    response = await anon_client.get("/")
    assert response.status_code == 307
    assert response.headers["location"] == WEB_INDEX


async def test_page_needs_no_token_but_the_api_still_does(anon_client: AsyncClient) -> None:
    """The shell loads unauthenticated; the data behind it does not."""
    assert (await anon_client.get(WEB_INDEX)).status_code == 200
    assert (await anon_client.get("/ui/app.js")).status_code == 200
    unauthenticated = await anon_client.get("/api/v1/assets")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error_code"] == "auth.credentials_required"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/ui/app.js", "javascript"),
        ("/ui/api.js", "javascript"),
        ("/ui/views/board.js", "javascript"),
        ("/ui/app.css", "text/css"),
        ("/ui/order-actions.json", "application/json"),
    ],
)
async def test_assets_carry_media_types_a_browser_will_execute(
    anon_client: AsyncClient, path: str, expected: str
) -> None:
    """Regression guard for the Windows ``.js`` -> ``text/plain`` registry mapping.

    Responses are served with ``nosniff``, and module scripts are subject to a
    strict media-type check with no sniffing fallback, so a wrong label here
    does not degrade the page -- it stops it running at all.
    """
    response = await anon_client.get(path)
    assert response.status_code == 200
    assert expected in response.headers["content-type"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.fixture
def platform_media_types() -> Iterator[None]:
    """Let a test corrupt the process-wide media-type table and put it back."""
    yield
    pin_media_types()


@pytest.mark.usefixtures("platform_media_types")
def test_pin_overrides_a_platform_mapping_that_would_break_the_page() -> None:
    """The pin must *override* the platform, not merely agree with it.

    Windows seeds :mod:`mimetypes` from the registry, where ``.js`` is
    commonly ``text/plain``. Reproduce exactly that mapping and show the pin
    wins, so the guarantee does not depend on which machine runs the suite.
    """
    mimetypes.add_type("text/plain", ".js")
    assert mimetypes.guess_type("app.js")[0] == "text/plain"

    pin_media_types()

    assert mimetypes.guess_type("app.js")[0] == "text/javascript"
    for suffix, media_type in PINNED_MEDIA_TYPES.items():
        assert mimetypes.guess_type(f"file{suffix}")[0] == media_type


async def test_every_shipped_file_is_reachable(anon_client: AsyncClient) -> None:
    assert SHIPPED_FILES, "the frontend directory is empty"
    for path in SHIPPED_FILES:
        route = path.relative_to(WEB_DIR).as_posix()
        response = await anon_client.get(f"/ui/{route}")
        assert response.status_code == 200, route


def test_every_shipped_file_is_declared_as_package_data() -> None:
    """A file the build does not ship is a file the deployed API cannot serve.

    Nothing generates this frontend, so the tree is the artefact; a new
    subdirectory or extension has to be added to ``package-data`` or it
    silently disappears from the wheel.
    """
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = pyproject["tool"]["setuptools"]["package-data"]["cfm"]
    for path in SHIPPED_FILES:
        route = path.relative_to(WEB_DIR).as_posix()
        assert any(fnmatch(f"web/{route}", pattern) for pattern in patterns), route


async def test_config_reports_the_settings_the_page_cannot_guess(anon_client: AsyncClient) -> None:
    response = await anon_client.get("/ui/config.json")
    assert response.status_code == 200
    payload = response.json()
    assert payload["api_prefix"] == "/api/v1"
    # The page shows whatever the running build reports, so this asserts the
    # wiring rather than a literal that every release would have to chase.
    assert payload["app_version"] == __version__
    assert payload["app_name"]


async def test_config_follows_a_relocated_api_prefix(database_url: str) -> None:
    """The page reads the prefix rather than hardcoding one, so this holds."""
    async with build_app(database_url, api_prefix="/api/v2") as application:
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as web_client:
            response = await web_client.get("/ui/config.json")
            assert response.json()["api_prefix"] == "/api/v2"


async def test_frontend_can_be_switched_off(database_url: str) -> None:
    async with build_app(database_url, web_enabled=False) as application:
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as bare_client:
            assert (await bare_client.get("/")).status_code == 404
            assert (await bare_client.get(WEB_INDEX)).status_code == 404
            assert (await bare_client.get("/ui/config.json")).status_code == 404
            assert (await bare_client.get("/api/v1/health")).status_code == 200


async def test_the_mount_does_not_shadow_the_api_problem_responses(
    anon_client: AsyncClient,
) -> None:
    """The frontend lives under its own prefix precisely so this stays true."""
    missing = await anon_client.get("/api/v1/does-not-exist")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["error_code"] == "api.http_error"

    unknown_asset = await anon_client.get("/ui/not-a-file.js")
    assert unknown_asset.status_code == 404


# --------------------------------------------------------------------------
# The API contract the order board depends on
# --------------------------------------------------------------------------


def test_order_actions_mirror_the_domain_state_machine() -> None:
    """The board offers exactly the transitions the domain allows.

    ``order-actions.json`` is the board's copy of
    :data:`cfm.domain.ORDER_TRANSITIONS`; this is what stops the copy drifting
    into offering a button the API would refuse, or hiding one it would accept.
    """
    statuses = {status.value for status in MaintenanceOrderStatus}
    assert set(ORDER_ACTIONS["statuses"]) == statuses
    assert set(ORDER_ACTIONS["labels"]) == statuses
    assert set(ORDER_ACTIONS["actions"]) == statuses

    for status, allowed in ORDER_TRANSITIONS.items():
        offered = {action["to"] for action in ORDER_ACTIONS["actions"][status.value]}
        assert offered == {target.value for target in allowed}, status.value


def test_order_actions_point_at_endpoints_the_api_publishes(tmp_path: Path) -> None:
    """Every button on a card maps to a POST the API documents."""
    openapi = inspection_app(tmp_path).openapi()
    for actions in ORDER_ACTIONS["actions"].values():
        for action in actions:
            path = f"/api/v1/maintenance-orders/{{order_id}}/{action['path']}"
            assert path in openapi["paths"], path
            assert "post" in openapi["paths"][path], path


def _request_schema(openapi: dict[str, Any], path: str) -> dict[str, Any] | None:
    """The JSON body schema of a POST operation, or None when it takes no body."""
    body = openapi["paths"][path]["post"].get("requestBody")
    if body is None:
        return None
    schema = body["content"]["application/json"]["schema"]
    references = [schema["$ref"]] if "$ref" in schema else []
    references += [entry["$ref"] for entry in schema.get("anyOf", []) if "$ref" in entry]
    if not references:
        return None
    name = references[0].rsplit("/", 1)[-1]
    resolved: dict[str, Any] = openapi["components"]["schemas"][name]
    return resolved


def test_order_action_fields_match_the_request_schemas(tmp_path: Path) -> None:
    """Whatever a card asks the operator to type is what the endpoint expects.

    The board marks the completion note as required and the cancellation
    reason as optional; both claims are checked against the API's own schema
    rather than against a comment.
    """
    openapi = inspection_app(tmp_path).openapi()
    for actions in ORDER_ACTIONS["actions"].values():
        for action in actions:
            path = f"/api/v1/maintenance-orders/{{order_id}}/{action['path']}"
            schema = _request_schema(openapi, path)
            field = action.get("field")
            if field is None:
                assert schema is None, action["path"]
                continue
            assert schema is not None, action["path"]
            assert field["name"] in schema["properties"], action["path"]
            required = field["name"] in schema.get("required", [])
            assert required == field["required"], action["path"]


async def test_the_views_read_only_endpoints_that_exist(client: AsyncClient) -> None:
    """Every list the three views load answers with the envelope they expect."""
    for path in ("/api/v1/locations", "/api/v1/assets", "/api/v1/maintenance-orders"):
        response = await client.get(path, params={"limit": 200, "offset": 0})
        assert response.status_code == 200, path
        assert set(response.json()) == {"items", "total", "limit", "offset"}, path

    audit = await client.get("/api/v1/audit-entries", params={"limit": 50, "offset": 0})
    assert audit.status_code == 200
    assert set(audit.json()) == {"items", "total", "limit", "offset"}

    me = await client.get("/api/v1/me")
    assert me.status_code == 200
    # The board decides whether to offer any action at all from this field.
    assert me.json()["role"] in {"viewer", "engineer", "admin"}


# --------------------------------------------------------------------------
# What the shipped JavaScript is allowed to do
# --------------------------------------------------------------------------


@pytest.mark.parametrize("script", SHIPPED_SCRIPTS, ids=lambda path: path.name)
def test_shipped_scripts_parse_as_modules(script: Path, tmp_path: Path) -> None:
    """Nothing else parses this code before a browser does, so parse it here.

    The file is copied to a ``.mjs`` name first, and that detail is the whole
    point: ``node --check`` treats a ``.js`` file as CommonJS, and on a file
    that turns out to contain ``import`` or ``export`` it exits 0 without
    parsing anything at all. Pointing it straight at these modules would be a
    check that can never fail.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable; nothing here can parse the modules")
    copy = tmp_path / f"{script.stem}.mjs"
    copy.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    # Fixed argument list, absolute interpreter path, no shell.
    result = subprocess.run(  # noqa: S603
        [node, "--check", str(copy)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "sink", [".innerHTML", ".outerHTML", "eval(", "new Function(", "document.write("]
)
def test_frontend_never_turns_api_text_into_markup(sink: str) -> None:
    """The page claims every API string reaches it as a text node. Hold it to that.

    Location names, order titles, and problem details all come from the API
    and all end up on screen; if any of them were assigned as HTML, a stored
    string would become executable markup.
    """
    for path in SHIPPED_SCRIPTS:
        assert sink not in path.read_text(encoding="utf-8"), f"{path.name} uses {sink}"


def test_frontend_module_imports_resolve() -> None:
    """No bundler checks these paths, so the suite does.

    A mistyped relative import is invisible until a browser refuses to load
    the module, which no Python test would otherwise notice.
    """
    pattern = 'from "'
    for path in SHIPPED_SCRIPTS:
        for line in path.read_text(encoding="utf-8").splitlines():
            index = line.find(pattern)
            if index == -1:
                continue
            target = line[index + len(pattern) :].split('"')[0]
            if not target.startswith("."):
                continue
            assert (path.parent / target).resolve().is_file(), f"{path.name} -> {target}"
