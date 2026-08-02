from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from cfm.models import ApiToken, User
from cfm.services.tokens import hash_token

from .conftest import ACTOR, ADMIN, VIEWER, create_hierarchy

pytestmark = pytest.mark.anyio


def auth_header_for_secret(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def _install_token(
    app: FastAPI,
    username: str,
    secret: str,
    *,
    expires_at: datetime | None = None,
    revoked: bool = False,
) -> None:
    """Plant a token row directly, bypassing the API, for 401-path tests."""
    with app.state.session_factory() as session:
        user_id = session.scalar(select(User.id).where(User.username == username))
        assert user_id is not None
        session.add(
            ApiToken(
                user_id=user_id,
                label="planted",
                token_hash=hash_token(secret),
                expires_at=expires_at,
                revoked=revoked,
            )
        )
        session.commit()


async def test_me_returns_the_authenticated_identity(client: AsyncClient) -> None:
    for headers, username, role in [
        (ADMIN, "test-admin", "admin"),
        (ACTOR, "test-engineer", "engineer"),
        (VIEWER, "test-viewer", "viewer"),
    ]:
        response = await client.get("/api/v1/me", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["username"] == username
        assert payload["role"] == role
        assert payload["is_active"] is True
        assert "token" not in payload


async def test_me_requires_authentication(anon_client: AsyncClient) -> None:
    response = await anon_client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth.credentials_required"


async def test_unknown_token_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/locations", headers={"Authorization": "Bearer no-such-secret"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error_code"] == "auth.invalid_token"


async def test_wrong_scheme_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/locations", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth.credentials_required"


async def test_revoked_token_rejected(app: FastAPI, client: AsyncClient) -> None:
    _install_token(app, "test-engineer", "revoked-secret", revoked=True)
    response = await client.get(
        "/api/v1/locations", headers=auth_header_for_secret("revoked-secret")
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth.invalid_token"


async def test_expired_token_rejected(app: FastAPI, client: AsyncClient) -> None:
    _install_token(
        app,
        "test-engineer",
        "expired-secret",
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    response = await client.get(
        "/api/v1/locations", headers=auth_header_for_secret("expired-secret")
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth.invalid_token"


async def test_unexpired_token_accepted(app: FastAPI, client: AsyncClient) -> None:
    _install_token(
        app,
        "test-engineer",
        "fresh-secret",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    response = await client.get("/api/v1/locations", headers=auth_header_for_secret("fresh-secret"))
    assert response.status_code == 200


async def test_inactive_user_token_rejected(client: AsyncClient) -> None:
    users = await client.get("/api/v1/users", headers=ADMIN)
    viewer = next(item for item in users.json()["items"] if item["username"] == "test-viewer")
    deactivated = await client.post(f"/api/v1/users/{viewer['id']}/deactivate", headers=ADMIN)
    assert deactivated.status_code == 200

    response = await client.get("/api/v1/locations", headers=VIEWER)
    assert response.status_code == 401
    assert response.json()["error_code"] == "auth.invalid_token"


async def test_viewer_reads_but_cannot_write(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)

    listing = await client.get("/api/v1/locations", headers=VIEWER)
    assert listing.status_code == 200
    assert listing.json()["total"] == 4

    audit = await client.get("/api/v1/audit-entries", headers=VIEWER)
    assert audit.status_code == 200

    created = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-V", "name": "Campus V"},
        headers=VIEWER,
    )
    assert created.status_code == 403
    assert created.json()["error_code"] == "auth.forbidden"

    patched = await client.patch(
        f"/api/v1/locations/{hierarchy['site']['id']}",
        json={"name": "Renamed"},
        headers=VIEWER,
    )
    assert patched.status_code == 403

    deleted = await client.delete(f"/api/v1/locations/{hierarchy['room']['id']}", headers=VIEWER)
    assert deleted.status_code == 403

    unchanged = await client.get("/api/v1/locations")
    assert unchanged.json()["total"] == 4


@pytest.mark.parametrize("headers_name", ["engineer", "viewer"])
async def test_user_management_requires_admin(client: AsyncClient, headers_name: str) -> None:
    headers = ACTOR if headers_name == "engineer" else VIEWER

    listing = await client.get("/api/v1/users", headers=headers)
    assert listing.status_code == 403
    assert listing.json()["error_code"] == "auth.forbidden"

    created = await client.post(
        "/api/v1/users",
        json={"username": "new-user", "display_name": "New User", "role": "viewer"},
        headers=headers,
    )
    assert created.status_code == 403

    tokens = await client.post(
        "/api/v1/users/any-id/tokens",
        json={"label": "nope"},
        headers=headers,
    )
    assert tokens.status_code == 403


async def test_admin_writes_domain_resources(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-Z", "name": "Campus Z"},
        headers=ADMIN,
    )
    assert response.status_code == 201
