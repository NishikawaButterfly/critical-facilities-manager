from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from cfm.config import Settings
from cfm.main import create_app

from .conftest import create_hierarchy

pytestmark = pytest.mark.anyio


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload == {"status": "ok", "version": "0.2.0", "database": "ok"}


async def test_unknown_route_returns_problem(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    payload = response.json()
    assert payload["status"] == 404
    assert payload["instance"] == "/api/v1/does-not-exist"
    assert payload["error_code"] == "api.http_error"
    assert set(payload) >= {"type", "title", "status", "detail", "instance", "error_code"}


async def test_request_validation_problem_shape(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-A"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    payload = response.json()
    assert payload["error_code"] == "api.validation_failed"
    assert any(param["name"].endswith("name") for param in payload["invalid_params"])


async def test_method_not_allowed_returns_problem(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/audit-entries")
    assert response.status_code == 405
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_unauthenticated_request_rejected_and_nothing_created(
    client: AsyncClient, anon_client: AsyncClient
) -> None:
    response = await anon_client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-A", "name": "Campus A"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error_code"] == "auth.credentials_required"

    listing = await client.get("/api/v1/locations")
    assert listing.json()["total"] == 0
    audit = await client.get("/api/v1/audit-entries")
    assert audit.json()["total"] == 0


async def test_health_stays_public(anon_client: AsyncClient) -> None:
    response = await anon_client.get("/api/v1/health")
    assert response.status_code == 200


async def test_docs_served_when_enabled(tmp_path: Path) -> None:
    database_path = (tmp_path / "docs-test.db").as_posix()
    settings = Settings(
        database_url=f"sqlite:///{database_path}", docs_enabled=True, db_auto_upgrade=True
    )
    application = create_app(settings)
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://testserver") as docs_client,
    ):
        response = await docs_client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        assert "/api/v1/health" in paths
        assert "/api/v1/me" in paths
        assert "/api/v1/users" in paths
        assert "/api/v1/users/{user_id}/tokens/{token_id}/revoke" in paths
        assert "/api/v1/assets/{asset_id}/transition" in paths
        assert "/api/v1/procedures/{procedure_id}/versions" in paths
        assert "/api/v1/work-permits/{permit_id}/issue" in paths
        assert "/api/v1/incidents/{incident_id}/corrective-order" in paths
        assert "/api/v1/commissioning-tests/{test_id}/punch-item" in paths
        assert "/api/v1/punch-items/{punch_item_id}/close" in paths
        assert "/api/v1/constraints/{constraint_id}/retire" in paths
        schemes = response.json()["components"]["securitySchemes"]
        assert any(scheme.get("scheme") == "bearer" for scheme in schemes.values())


async def test_pagination_envelope_and_limits(client: AsyncClient) -> None:
    await create_hierarchy(client)
    response = await client.get("/api/v1/locations", params={"limit": 2, "offset": 1})
    payload = response.json()
    assert payload["total"] == 4
    assert payload["limit"] == 2
    assert payload["offset"] == 1
    assert len(payload["items"]) == 2

    invalid = await client.get("/api/v1/locations", params={"limit": 0})
    assert invalid.status_code == 422
    too_large = await client.get("/api/v1/locations", params={"limit": 500})
    assert too_large.status_code == 422
