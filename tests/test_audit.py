from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import ACTOR, ADMIN, create_asset, create_hierarchy, create_order

pytestmark = pytest.mark.anyio


async def _entries(client: AsyncClient, **params: str) -> list[dict[str, Any]]:
    response = await client.get("/api/v1/audit-entries", params=params)
    assert response.status_code == 200
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def test_create_emits_audit_entry(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    entries = await _entries(client, entity_type="location", entity_id=hierarchy["site"]["id"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "created"
    assert entry["actor"] == "test-engineer"
    assert entry["before"] is None
    assert entry["after"]["code"] == hierarchy["site"]["code"]
    assert entry["after"]["kind"] == "site"
    assert entry["occurred_at"].endswith("Z") or "+00:00" in entry["occurred_at"]


async def test_update_audit_records_changed_fields_only(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    await client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"name": "UPS System 1B", "asset_type": "ups"},
        headers=ACTOR,
    )
    entries = await _entries(client, entity_type="asset", entity_id=asset["id"])
    updated = [entry for entry in entries if entry["action"] == "updated"]
    assert len(updated) == 1
    assert updated[0]["before"] == {"name": "UPS System 1"}
    assert updated[0]["after"] == {"name": "UPS System 1B"}


async def test_noop_update_emits_no_audit_entry(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    await client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"name": "UPS System 1"},
        headers=ACTOR,
    )
    entries = await _entries(client, entity_type="asset", entity_id=asset["id"])
    assert [entry["action"] for entry in entries] == ["created"]


async def test_transition_audit_records_before_and_after_status(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    await client.post(
        f"/api/v1/assets/{asset['id']}/transition",
        json={"to_status": "installed"},
        headers=ACTOR,
    )
    entries = await _entries(client, entity_type="asset", entity_id=asset["id"])
    transition = entries[0]
    assert transition["action"] == "status_changed"
    assert transition["before"] == {"status": "planned"}
    assert transition["after"] == {"status": "installed"}


async def test_order_completion_audit_includes_notes(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"], status="operational")
    order = await create_order(client, asset["id"])
    order_id = order["id"]
    await client.post(f"/api/v1/maintenance-orders/{order_id}/schedule", headers=ACTOR)
    await client.post(f"/api/v1/maintenance-orders/{order_id}/start", headers=ACTOR)
    await client.post(
        f"/api/v1/maintenance-orders/{order_id}/complete",
        json={"completion_notes": "All parameters nominal."},
        headers=ACTOR,
    )
    entries = await _entries(client, entity_type="maintenance_order", entity_id=order_id)
    completion = entries[0]
    assert completion["action"] == "status_changed"
    assert completion["before"] == {"status": "in_progress", "completion_notes": None}
    assert completion["after"] == {
        "status": "done",
        "completion_notes": "All parameters nominal.",
    }


async def test_delete_audit_keeps_before_snapshot(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    room_id = hierarchy["room"]["id"]
    await client.delete(f"/api/v1/locations/{room_id}", headers=ACTOR)
    entries = await _entries(client, entity_type="location", entity_id=room_id)
    deletion = entries[0]
    assert deletion["action"] == "deleted"
    assert deletion["before"]["code"] == "L1-DH1"
    assert deletion["after"] is None


async def test_audit_filters_by_actor_and_entity_type(client: AsyncClient) -> None:
    await create_hierarchy(client)
    response = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-B", "name": "Campus B"},
        headers=ADMIN,
    )
    assert response.status_code == 201

    by_actor = await _entries(client, actor="test-admin")
    assert len(by_actor) == 1
    assert by_actor[0]["after"]["code"] == "CAMP-B"

    by_type = await client.get("/api/v1/audit-entries", params={"entity_type": "location"})
    assert by_type.json()["total"] == 5


async def test_audit_entries_are_newest_first_and_paginated(client: AsyncClient) -> None:
    await create_hierarchy(client)
    response = await client.get("/api/v1/audit-entries", params={"limit": 2})
    payload = response.json()
    assert payload["total"] == 4
    assert len(payload["items"]) == 2
    identifiers = [entry["id"] for entry in payload["items"]]
    assert identifiers == sorted(identifiers, reverse=True)
    # The newest entry is the room creation.
    assert payload["items"][0]["after"]["kind"] == "room"


async def test_audit_trail_has_no_write_endpoints(client: AsyncClient) -> None:
    for method, url in [
        ("POST", "/api/v1/audit-entries"),
        ("DELETE", "/api/v1/audit-entries"),
        ("DELETE", "/api/v1/audit-entries/1"),
        ("PATCH", "/api/v1/audit-entries/1"),
    ]:
        response = await client.request(method, url, headers=ACTOR)
        assert response.status_code in {404, 405}
