from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from cfm.bootstrap import BootstrapError, bootstrap_admin
from cfm.config import Settings
from cfm.errors import RuleViolationError
from cfm.main import create_app
from cfm.services.tokens import hash_token

from .conftest import ADMIN

pytestmark = pytest.mark.anyio


async def _create_user(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "username": "jordan-eng",
        "display_name": "Jordan Blake",
        "role": "engineer",
    }
    body.update(overrides)
    response = await client.post("/api/v1/users", json=body, headers=ADMIN)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def test_create_user_and_fetch_it(client: AsyncClient) -> None:
    user = await _create_user(client)
    assert user["username"] == "jordan-eng"
    assert user["display_name"] == "Jordan Blake"
    assert user["role"] == "engineer"
    assert user["is_active"] is True

    fetched = await client.get(f"/api/v1/users/{user['id']}", headers=ADMIN)
    assert fetched.status_code == 200
    assert fetched.json() == user

    audit = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "user", "entity_id": user["id"]},
    )
    entry = audit.json()["items"][0]
    assert entry["action"] == "created"
    assert entry["actor"] == "test-admin"


async def test_duplicate_username_rejected(client: AsyncClient) -> None:
    await _create_user(client)
    response = await client.post(
        "/api/v1/users",
        json={"username": "jordan-eng", "display_name": "Another", "role": "viewer"},
        headers=ADMIN,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "user.username_taken"


@pytest.mark.parametrize("username", ["Jordan", "jordan blake", "-jordan", "jordan-", "jordan_b"])
async def test_invalid_usernames_rejected(client: AsyncClient, username: str) -> None:
    response = await client.post(
        "/api/v1/users",
        json={"username": username, "display_name": "Jordan", "role": "viewer"},
        headers=ADMIN,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "api.validation_failed"


async def test_list_users_with_filters(client: AsyncClient) -> None:
    await _create_user(client)
    everyone = await client.get("/api/v1/users", headers=ADMIN)
    assert everyone.json()["total"] == 6

    engineers = await client.get("/api/v1/users", params={"role": "engineer"}, headers=ADMIN)
    assert engineers.json()["total"] == 4

    user = next(item for item in everyone.json()["items"] if item["username"] == "jordan-eng")
    await client.post(f"/api/v1/users/{user['id']}/deactivate", headers=ADMIN)
    inactive = await client.get("/api/v1/users", params={"is_active": "false"}, headers=ADMIN)
    assert [item["username"] for item in inactive.json()["items"]] == ["jordan-eng"]


async def test_get_missing_user(client: AsyncClient) -> None:
    response = await client.get("/api/v1/users/missing-id", headers=ADMIN)
    assert response.status_code == 404
    assert response.json()["error_code"] == "user.not_found"


async def test_deactivate_is_idempotent_only_once(client: AsyncClient) -> None:
    user = await _create_user(client)
    first = await client.post(f"/api/v1/users/{user['id']}/deactivate", headers=ADMIN)
    assert first.status_code == 200
    assert first.json()["is_active"] is False

    again = await client.post(f"/api/v1/users/{user['id']}/deactivate", headers=ADMIN)
    assert again.status_code == 409
    assert again.json()["error_code"] == "user.already_inactive"


async def test_last_active_admin_cannot_be_deactivated(client: AsyncClient) -> None:
    users = await client.get("/api/v1/users", headers=ADMIN)
    admin = next(item for item in users.json()["items"] if item["role"] == "admin")
    response = await client.post(f"/api/v1/users/{admin['id']}/deactivate", headers=ADMIN)
    assert response.status_code == 409
    assert response.json()["error_code"] == "user.last_active_admin"


async def test_second_admin_allows_deactivation(client: AsyncClient) -> None:
    second = await _create_user(client, username="second-admin", role="admin")
    users = await client.get("/api/v1/users", headers=ADMIN)
    first = next(item for item in users.json()["items"] if item["username"] == "test-admin")
    response = await client.post(f"/api/v1/users/{first['id']}/deactivate", headers=ADMIN)
    assert response.status_code == 200
    assert second["is_active"] is True


async def test_token_secret_is_returned_once_and_stored_hashed(
    client: AsyncClient, database_url: str
) -> None:
    user = await _create_user(client)
    created = await client.post(
        f"/api/v1/users/{user['id']}/tokens",
        json={"label": "ci"},
        headers=ADMIN,
    )
    assert created.status_code == 201
    payload = created.json()
    secret = payload["token"]
    assert len(secret) > 30
    assert payload["label"] == "ci"
    assert payload["revoked"] is False
    assert payload["expires_at"] is None

    listing = await client.get(f"/api/v1/users/{user['id']}/tokens", headers=ADMIN)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == payload["id"]
    assert "token" not in items[0]
    assert "token_hash" not in items[0]

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT * FROM api_tokens")).mappings().all()
    finally:
        engine.dispose()
    dump = str(rows)
    assert secret not in dump
    assert hash_token(secret) in dump

    audit = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "api_token", "entity_id": payload["id"]},
    )
    assert secret not in audit.text
    assert hash_token(secret) not in audit.text

    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {secret}"})
    assert me.status_code == 200
    assert me.json()["username"] == "jordan-eng"


async def test_revoked_token_stops_working(client: AsyncClient) -> None:
    user = await _create_user(client)
    created = await client.post(
        f"/api/v1/users/{user['id']}/tokens",
        json={"label": "short-lived"},
        headers=ADMIN,
    )
    token_id = created.json()["id"]
    secret = created.json()["token"]

    revoked = await client.post(
        f"/api/v1/users/{user['id']}/tokens/{token_id}/revoke", headers=ADMIN
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True

    rejected = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {secret}"})
    assert rejected.status_code == 401

    again = await client.post(f"/api/v1/users/{user['id']}/tokens/{token_id}/revoke", headers=ADMIN)
    assert again.status_code == 409
    assert again.json()["error_code"] == "api_token.already_revoked"


async def test_token_rules(client: AsyncClient) -> None:
    user = await _create_user(client)

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    rejected = await client.post(
        f"/api/v1/users/{user['id']}/tokens",
        json={"label": "stale", "expires_at": past},
        headers=ADMIN,
    )
    assert rejected.status_code == 422
    assert rejected.json()["error_code"] == "api_token.expiry_not_future"

    missing = await client.post(
        "/api/v1/users/missing-id/tokens", json={"label": "x"}, headers=ADMIN
    )
    assert missing.status_code == 404

    await client.post(f"/api/v1/users/{user['id']}/deactivate", headers=ADMIN)
    inactive = await client.post(
        f"/api/v1/users/{user['id']}/tokens", json={"label": "x"}, headers=ADMIN
    )
    assert inactive.status_code == 422
    assert inactive.json()["error_code"] == "api_token.user_inactive"


async def test_revoking_another_users_token_is_not_found(client: AsyncClient) -> None:
    owner = await _create_user(client)
    other = await _create_user(client, username="casey-eng")
    created = await client.post(
        f"/api/v1/users/{owner['id']}/tokens", json={"label": "ci"}, headers=ADMIN
    )
    token_id = created.json()["id"]

    response = await client.post(
        f"/api/v1/users/{other['id']}/tokens/{token_id}/revoke", headers=ADMIN
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "api_token.not_found"


async def test_bootstrap_creates_the_first_admin(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'boot.db').as_posix()}"
    result = bootstrap_admin("first-admin", "First Admin", database_url=database_url)
    assert result.username == "first-admin"
    assert len(result.token) > 30

    settings = Settings(database_url=database_url, docs_enabled=False)
    application = create_app(settings)
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {result.token}"})
        assert me.status_code == 200
        assert me.json()["username"] == "first-admin"
        assert me.json()["role"] == "admin"


def test_bootstrap_refuses_when_users_exist(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'boot.db').as_posix()}"
    bootstrap_admin("first-admin", "First Admin", database_url=database_url)
    with pytest.raises(BootstrapError):
        bootstrap_admin("second-admin", "Second Admin", database_url=database_url)


def test_bootstrap_validates_the_username(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'boot.db').as_posix()}"
    with pytest.raises(RuleViolationError):
        bootstrap_admin("Not A Slug", "Broken", database_url=database_url)
