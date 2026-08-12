from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from cfm.config import Settings
from cfm.main import create_app
from cfm.models import ApiToken, SiteGrant, User
from cfm.services.tokens import hash_token

TEST_USERS: tuple[tuple[str, str, str], ...] = (
    ("test-admin", "Test Admin", "admin"),
    ("test-engineer", "Test Engineer", "engineer"),
    ("test-approver", "Test Approver", "engineer"),
    ("test-witness", "Test Witness", "engineer"),
    ("test-viewer", "Test Viewer", "viewer"),
)


def token_for(username: str) -> str:
    """The deterministic token secret the fixture stores (hashed) per user."""
    return f"secret-for-{username}"


def auth_header(username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(username)}"}


ADMIN = auth_header("test-admin")
ACTOR = auth_header("test-engineer")
APPROVER = auth_header("test-approver")
VIEWER = auth_header("test-viewer")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@asynccontextmanager
async def build_app(database_url: str, **settings_overrides: Any) -> AsyncIterator[FastAPI]:
    """A migrated, seeded application; overrides feed extra Settings fields.

    The evidence store defaults to a directory next to the SQLite database
    file, so no test ever writes evidence into the working directory.
    """
    if "evidence_dir" not in settings_overrides and database_url.startswith("sqlite:///"):
        database_path = Path(database_url.removeprefix("sqlite:///"))
        settings_overrides["evidence_dir"] = database_path.parent / "evidence"
    settings = Settings(
        database_url=database_url,
        docs_enabled=False,
        db_auto_upgrade=True,
        **settings_overrides,
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        with application.state.session_factory() as session:
            for username, display_name, role in TEST_USERS:
                user = User(
                    username=username,
                    display_name=display_name,
                    role=role,
                    is_active=True,
                )
                session.add(user)
                session.flush()
                session.add(
                    ApiToken(
                        user_id=user.id,
                        label="test",
                        token_hash=hash_token(token_for(username)),
                        expires_at=None,
                        revoked=False,
                    )
                )
                # The installation-wide grant every user held before site
                # scoping existed; scoped users are created per test.
                session.add(SiteGrant(user_id=user.id, site_id=None))
            session.commit()
        yield application


@pytest.fixture
async def app(database_url: str) -> AsyncIterator[FastAPI]:
    async with build_app(database_url) as application:
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client authenticated as the engineer; per-request headers override it."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", headers=ACTOR
    ) as test_client:
        yield test_client


@pytest.fixture
async def anon_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client that sends no credentials at all."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


async def create_scoped_user(
    client: AsyncClient,
    username: str,
    *,
    role: str = "engineer",
    site_ids: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Create a user (optionally site-scoped), mint a token, return (user, headers)."""
    body: dict[str, Any] = {
        "username": username,
        "display_name": username.replace("-", " ").title(),
        "role": role,
    }
    if site_ids is not None:
        body["site_ids"] = site_ids
    created = await client.post("/api/v1/users", json=body, headers=ADMIN)
    assert created.status_code == 201, created.text
    user: dict[str, Any] = created.json()
    minted = await client.post(
        f"/api/v1/users/{user['id']}/tokens", json={"label": "test"}, headers=ADMIN
    )
    assert minted.status_code == 201, minted.text
    return user, {"Authorization": f"Bearer {minted.json()['token']}"}


async def create_location(
    client: AsyncClient,
    kind: str,
    code: str,
    name: str,
    parent_id: str | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/locations",
        json={"kind": kind, "code": code, "name": name, "parent_id": parent_id},
        headers=ACTOR,
    )
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def create_hierarchy(client: AsyncClient, suffix: str = "A") -> dict[str, dict[str, Any]]:
    site = await create_location(client, "site", f"CAMP-{suffix}", f"Campus {suffix}")
    building = await create_location(
        client, "building", f"BLDG-{suffix}", f"Building {suffix}", site["id"]
    )
    floor = await create_location(client, "floor", "L1", "Level 1", building["id"])
    room = await create_location(client, "room", "L1-DH1", "Data Hall 1", floor["id"])
    return {"site": site, "building": building, "floor": floor, "room": room}


async def create_asset(
    client: AsyncClient,
    location_id: str,
    tag: str = "UPS-C-01",
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "tag": tag,
        "name": "UPS System 1",
        "asset_type": "ups",
        "criticality": "critical",
        "location_id": location_id,
    }
    body.update(overrides)
    response = await client.post("/api/v1/assets", json=body, headers=ACTOR)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def create_order(
    client: AsyncClient,
    asset_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "order_type": "preventive",
        "asset_id": asset_id,
        "title": "Quarterly inspection",
        "due_date": "2026-09-01",
    }
    body.update(overrides)
    response = await client.post("/api/v1/maintenance-orders", json=body, headers=ACTOR)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def create_procedure(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "mop",
        "title": "Fan module replacement",
        "steps": [
            "Isolate and lock out the fan module power feed.",
            "Swap the fan module.",
            "Restore power and verify airflow.",
        ],
    }
    body.update(overrides)
    response = await client.post("/api/v1/procedures", json=body, headers=ACTOR)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def create_approved_procedure(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    procedure = await create_procedure(client, **overrides)
    response = await client.post(f"/api/v1/procedures/{procedure['id']}/approve", headers=APPROVER)
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def create_permit(client: AsyncClient, order_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "order_id": order_id,
        "scope": "Work confined to the unit enclosure.",
    }
    body.update(overrides)
    response = await client.post("/api/v1/work-permits", json=body, headers=ACTOR)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def create_incident(client: AsyncClient, asset_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "asset_id": asset_id,
        "severity": "high",
        "title": "Unit tripped on high discharge pressure",
    }
    body.update(overrides)
    response = await client.post("/api/v1/incidents", json=body, headers=ACTOR)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def create_commissioning_test(
    client: AsyncClient, asset_id: str, **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "asset_id": asset_id,
        "title": "Integrated load transfer test",
        "planned_date": "2026-08-20",
    }
    body.update(overrides)
    response = await client.post("/api/v1/commissioning-tests", json=body, headers=ACTOR)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def create_punch_item(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "category": "defect",
        "severity": "major",
        "title": "Fan guard missing on the condenser section",
    }
    body.update(overrides)
    response = await client.post("/api/v1/punch-items", json=body, headers=ACTOR)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def create_constraint(
    client: AsyncClient, asset_ids: list[str], **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "mutual_exclusive_maintenance",
        "name": "Redundant cooling pair",
        "asset_ids": asset_ids,
    }
    body.update(overrides)
    response = await client.post("/api/v1/constraints", json=body, headers=ACTOR)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload
