from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import ACTOR, create_asset, create_hierarchy, create_order

pytestmark = pytest.mark.anyio


async def _asset(client: AsyncClient) -> dict[str, Any]:
    hierarchy = await create_hierarchy(client)
    return await create_asset(client, hierarchy["room"]["id"], status="operational")


async def test_create_order_starts_as_draft(client: AsyncClient) -> None:
    asset = await _asset(client)
    order = await create_order(client, asset["id"])
    assert order["status"] == "draft"
    assert order["order_type"] == "preventive"
    assert order["due_date"] == "2026-09-01"
    assert order["completion_notes"] is None


async def test_create_order_with_unknown_asset(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/maintenance-orders",
        json={
            "order_type": "corrective",
            "asset_id": "missing-id",
            "title": "Fix it",
            "due_date": "2026-09-01",
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "maintenance_order.asset_not_found"


async def test_full_lifecycle_to_done(client: AsyncClient) -> None:
    asset = await _asset(client)
    order = await create_order(client, asset["id"])
    order_id = order["id"]

    scheduled = await client.post(f"/api/v1/maintenance-orders/{order_id}/schedule", headers=ACTOR)
    assert scheduled.status_code == 200
    assert scheduled.json()["status"] == "scheduled"

    started = await client.post(f"/api/v1/maintenance-orders/{order_id}/start", headers=ACTOR)
    assert started.status_code == 200
    assert started.json()["status"] == "in_progress"

    completed = await client.post(
        f"/api/v1/maintenance-orders/{order_id}/complete",
        json={"completion_notes": "All parameters nominal."},
        headers=ACTOR,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "done"
    assert completed.json()["completion_notes"] == "All parameters nominal."


async def test_complete_requires_notes(client: AsyncClient) -> None:
    asset = await _asset(client)
    order = await create_order(client, asset["id"])
    order_id = order["id"]
    await client.post(f"/api/v1/maintenance-orders/{order_id}/schedule", headers=ACTOR)
    await client.post(f"/api/v1/maintenance-orders/{order_id}/start", headers=ACTOR)

    missing_body = await client.post(
        f"/api/v1/maintenance-orders/{order_id}/complete", headers=ACTOR
    )
    assert missing_body.status_code == 422
    assert missing_body.json()["error_code"] == "api.validation_failed"

    blank_notes = await client.post(
        f"/api/v1/maintenance-orders/{order_id}/complete",
        json={"completion_notes": "   "},
        headers=ACTOR,
    )
    assert blank_notes.status_code == 422
    assert blank_notes.json()["error_code"] == "maintenance_order.completion_notes_required"

    unchanged = await client.get(f"/api/v1/maintenance-orders/{order_id}")
    assert unchanged.json()["status"] == "in_progress"


@pytest.mark.parametrize(
    ("action", "prepare"),
    [
        ("start", []),
        ("complete", []),
        ("complete", ["schedule"]),
        ("schedule", ["schedule"]),
        ("schedule", ["schedule", "start"]),
    ],
)
async def test_illegal_order_transitions_rejected(
    client: AsyncClient, action: str, prepare: list[str]
) -> None:
    asset = await _asset(client)
    order = await create_order(client, asset["id"])
    order_id = order["id"]
    for step in prepare:
        response = await client.post(f"/api/v1/maintenance-orders/{order_id}/{step}", headers=ACTOR)
        assert response.status_code == 200

    body = {"completion_notes": "Done."} if action == "complete" else None
    response = await client.post(
        f"/api/v1/maintenance-orders/{order_id}/{action}", json=body, headers=ACTOR
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "maintenance_order.invalid_transition"


async def test_terminal_states_reject_all_transitions(client: AsyncClient) -> None:
    asset = await _asset(client)
    order = await create_order(client, asset["id"])
    order_id = order["id"]
    await client.post(f"/api/v1/maintenance-orders/{order_id}/cancel", headers=ACTOR)

    for action in ["schedule", "start", "cancel"]:
        response = await client.post(
            f"/api/v1/maintenance-orders/{order_id}/{action}", headers=ACTOR
        )
        assert response.status_code == 409
        assert response.json()["error_code"] == "maintenance_order.invalid_transition"


@pytest.mark.parametrize("prepare", [[], ["schedule"], ["schedule", "start"]])
async def test_cancel_from_active_states(client: AsyncClient, prepare: list[str]) -> None:
    asset = await _asset(client)
    order = await create_order(client, asset["id"])
    order_id = order["id"]
    for step in prepare:
        await client.post(f"/api/v1/maintenance-orders/{order_id}/{step}", headers=ACTOR)

    response = await client.post(
        f"/api/v1/maintenance-orders/{order_id}/cancel",
        json={"reason": "Superseded by another work order."},
        headers=ACTOR,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["completion_notes"] == "Superseded by another work order."


async def test_update_only_in_editable_states(client: AsyncClient) -> None:
    asset = await _asset(client)
    order = await create_order(client, asset["id"])
    order_id = order["id"]

    updated = await client.patch(
        f"/api/v1/maintenance-orders/{order_id}",
        json={"title": "Quarterly inspection (revised)", "due_date": "2026-10-01"},
        headers=ACTOR,
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Quarterly inspection (revised)"
    assert updated.json()["due_date"] == "2026-10-01"

    await client.post(f"/api/v1/maintenance-orders/{order_id}/schedule", headers=ACTOR)
    await client.post(f"/api/v1/maintenance-orders/{order_id}/start", headers=ACTOR)
    rejected = await client.patch(
        f"/api/v1/maintenance-orders/{order_id}",
        json={"title": "Too late"},
        headers=ACTOR,
    )
    assert rejected.status_code == 409
    assert rejected.json()["error_code"] == "maintenance_order.not_editable"


async def test_get_missing_order(client: AsyncClient) -> None:
    response = await client.get("/api/v1/maintenance-orders/missing-id")
    assert response.status_code == 404
    assert response.json()["error_code"] == "maintenance_order.not_found"


async def test_list_orders_with_filters(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset_1 = await create_asset(client, hierarchy["room"]["id"], status="operational")
    asset_2 = await create_asset(
        client, hierarchy["room"]["id"], tag="CRAC-C-01", asset_type="crac"
    )
    await create_order(client, asset_1["id"])
    order = await create_order(client, asset_2["id"], order_type="corrective", title="Fix fan")
    await client.post(f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR)

    by_asset = await client.get("/api/v1/maintenance-orders", params={"asset_id": asset_1["id"]})
    assert by_asset.json()["total"] == 1

    by_status = await client.get("/api/v1/maintenance-orders", params={"status": "scheduled"})
    assert by_status.json()["total"] == 1
    assert by_status.json()["items"][0]["title"] == "Fix fan"

    by_type = await client.get("/api/v1/maintenance-orders", params={"order_type": "preventive"})
    assert by_type.json()["total"] == 1
