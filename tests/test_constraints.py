from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import ACTOR, create_asset, create_constraint, create_hierarchy, create_order

pytestmark = pytest.mark.anyio


async def _two_assets(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    hierarchy = await create_hierarchy(client)
    first = await create_asset(client, hierarchy["room"]["id"], tag="UPS-C-01")
    second = await create_asset(client, hierarchy["room"]["id"], tag="UPS-C-02")
    return first, second


async def _started_order(client: AsyncClient, asset_id: str, **overrides: Any) -> dict[str, Any]:
    order = await create_order(client, asset_id, **overrides)
    scheduled = await client.post(
        f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR
    )
    assert scheduled.status_code == 200, scheduled.text
    started = await client.post(f"/api/v1/maintenance-orders/{order['id']}/start", headers=ACTOR)
    assert started.status_code == 200, started.text
    payload: dict[str, Any] = started.json()
    return payload


async def test_create_enforced_constraint(client: AsyncClient) -> None:
    first, second = await _two_assets(client)
    constraint = await create_constraint(client, [first["id"], second["id"]])
    assert constraint["kind"] == "mutual_exclusive_maintenance"
    assert constraint["status"] == "active"
    assert constraint["name"] == "Redundant cooling pair"
    assert constraint["description"] is None
    assert constraint["asset_ids"] == [first["id"], second["id"]]


async def test_enforced_constraint_needs_two_members(client: AsyncClient) -> None:
    first, _ = await _two_assets(client)
    response = await client.post(
        "/api/v1/constraints",
        json={
            "kind": "mutual_exclusive_maintenance",
            "name": "Lonely pair",
            "asset_ids": [first["id"]],
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "constraint.too_few_members"


async def test_advisory_constraint_with_free_text(client: AsyncClient) -> None:
    first, _ = await _two_assets(client)
    constraint = await create_constraint(
        client,
        [first["id"]],
        kind="advisory",
        name="Summer maintenance window",
        description="Prefer maintaining this unit outside the summer peak window.",
    )
    assert constraint["kind"] == "advisory"
    assert constraint["description"] == (
        "Prefer maintaining this unit outside the summer peak window."
    )


@pytest.mark.parametrize("description", [None, "   "])
async def test_advisory_requires_a_description(
    client: AsyncClient, description: str | None
) -> None:
    first, _ = await _two_assets(client)
    response = await client.post(
        "/api/v1/constraints",
        json={
            "kind": "advisory",
            "name": "Empty guidance",
            "description": description,
            "asset_ids": [first["id"]],
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "constraint.description_required"


async def test_duplicate_members_rejected(client: AsyncClient) -> None:
    first, _ = await _two_assets(client)
    response = await client.post(
        "/api/v1/constraints",
        json={
            "kind": "mutual_exclusive_maintenance",
            "name": "Twice the same asset",
            "asset_ids": [first["id"], first["id"]],
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "constraint.duplicate_member"


async def test_unknown_member_asset_rejected(client: AsyncClient) -> None:
    first, _ = await _two_assets(client)
    response = await client.post(
        "/api/v1/constraints",
        json={
            "kind": "mutual_exclusive_maintenance",
            "name": "Half real",
            "asset_ids": [first["id"], "missing-id"],
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "constraint.asset_not_found"


async def test_empty_member_list_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/constraints",
        json={"kind": "advisory", "name": "No members", "asset_ids": []},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "api.validation_failed"


async def test_retire_constraint(client: AsyncClient) -> None:
    first, second = await _two_assets(client)
    constraint = await create_constraint(client, [first["id"], second["id"]])

    retired = await client.post(f"/api/v1/constraints/{constraint['id']}/retire", headers=ACTOR)
    assert retired.status_code == 200
    assert retired.json()["status"] == "retired"


async def test_retire_twice_rejected(client: AsyncClient) -> None:
    first, second = await _two_assets(client)
    constraint = await create_constraint(client, [first["id"], second["id"]])
    await client.post(f"/api/v1/constraints/{constraint['id']}/retire", headers=ACTOR)

    response = await client.post(f"/api/v1/constraints/{constraint['id']}/retire", headers=ACTOR)
    assert response.status_code == 409
    assert response.json()["error_code"] == "constraint.invalid_transition"


async def test_list_constraints_with_filters(client: AsyncClient) -> None:
    first, second = await _two_assets(client)
    await create_constraint(client, [first["id"], second["id"]], name="UPS redundancy pair")
    advisory = await create_constraint(
        client,
        [second["id"]],
        kind="advisory",
        name="Handle with care",
        description="Vendor asks for a soft start after any shutdown.",
    )
    await client.post(f"/api/v1/constraints/{advisory['id']}/retire", headers=ACTOR)

    by_kind = await client.get("/api/v1/constraints", params={"kind": "advisory"})
    assert by_kind.json()["total"] == 1
    assert by_kind.json()["items"][0]["name"] == "Handle with care"

    by_status = await client.get("/api/v1/constraints", params={"status": "active"})
    assert by_status.json()["total"] == 1
    assert by_status.json()["items"][0]["name"] == "UPS redundancy pair"

    for_first = await client.get("/api/v1/constraints", params={"asset_id": first["id"]})
    assert for_first.json()["total"] == 1

    for_second = await client.get("/api/v1/constraints", params={"asset_id": second["id"]})
    assert for_second.json()["total"] == 2


async def test_mutual_exclusion_blocks_start(client: AsyncClient) -> None:
    first, second = await _two_assets(client)
    constraint = await create_constraint(
        client, [first["id"], second["id"]], name="UPS redundancy pair"
    )
    conflicting = await _started_order(client, first["id"], title="UPS 1 battery replacement")

    order = await create_order(client, second["id"], title="UPS 2 inspection")
    await client.post(f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR)
    response = await client.post(f"/api/v1/maintenance-orders/{order['id']}/start", headers=ACTOR)
    assert response.status_code == 409
    payload = response.json()
    assert payload["error_code"] == "maintenance_order.mutual_exclusion_conflict"
    assert "UPS redundancy pair" in payload["detail"]
    assert constraint["id"] in payload["detail"]
    assert conflicting["id"] in payload["detail"]

    unchanged = await client.get(f"/api/v1/maintenance-orders/{order['id']}")
    assert unchanged.json()["status"] == "scheduled"


async def test_start_allowed_after_sibling_order_done(client: AsyncClient) -> None:
    first, second = await _two_assets(client)
    await create_constraint(client, [first["id"], second["id"]])
    conflicting = await _started_order(client, first["id"])
    done = await client.post(
        f"/api/v1/maintenance-orders/{conflicting['id']}/complete",
        json={"completion_notes": "Replaced the battery string; load test passed."},
        headers=ACTOR,
    )
    assert done.status_code == 200

    order = await create_order(client, second["id"], title="UPS 2 inspection")
    await client.post(f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR)
    started = await client.post(f"/api/v1/maintenance-orders/{order['id']}/start", headers=ACTOR)
    assert started.status_code == 200
    assert started.json()["status"] == "in_progress"


async def test_start_allowed_when_constraint_retired(client: AsyncClient) -> None:
    first, second = await _two_assets(client)
    constraint = await create_constraint(client, [first["id"], second["id"]])
    await _started_order(client, first["id"])
    await client.post(f"/api/v1/constraints/{constraint['id']}/retire", headers=ACTOR)

    order = await create_order(client, second["id"], title="UPS 2 inspection")
    await client.post(f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR)
    started = await client.post(f"/api/v1/maintenance-orders/{order['id']}/start", headers=ACTOR)
    assert started.status_code == 200


async def test_advisory_is_not_enforced(client: AsyncClient) -> None:
    first, second = await _two_assets(client)
    await create_constraint(
        client,
        [first["id"], second["id"]],
        kind="advisory",
        name="Prefer one at a time",
        description="Guidance only: stagger work on these units when possible.",
    )
    await _started_order(client, first["id"])

    order = await create_order(client, second["id"], title="UPS 2 inspection")
    await client.post(f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR)
    started = await client.post(f"/api/v1/maintenance-orders/{order['id']}/start", headers=ACTOR)
    assert started.status_code == 200


async def test_non_member_asset_unaffected(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    first = await create_asset(client, hierarchy["room"]["id"], tag="UPS-C-01")
    second = await create_asset(client, hierarchy["room"]["id"], tag="UPS-C-02")
    outsider = await create_asset(client, hierarchy["room"]["id"], tag="CRAC-C-01")
    await create_constraint(client, [first["id"], second["id"]])
    await _started_order(client, first["id"])

    order = await create_order(client, outsider["id"], title="CRAC filter change")
    await client.post(f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR)
    started = await client.post(f"/api/v1/maintenance-orders/{order['id']}/start", headers=ACTOR)
    assert started.status_code == 200


async def test_second_order_on_the_same_asset_not_blocked(client: AsyncClient) -> None:
    first, second = await _two_assets(client)
    await create_constraint(client, [first["id"], second["id"]])
    await _started_order(client, first["id"])

    # The exclusion is between member assets, not between orders on one asset.
    order = await create_order(client, first["id"], title="UPS 1 follow-up check")
    await client.post(f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR)
    started = await client.post(f"/api/v1/maintenance-orders/{order['id']}/start", headers=ACTOR)
    assert started.status_code == 200


async def test_constraint_audit_trail(client: AsyncClient) -> None:
    first, second = await _two_assets(client)
    constraint = await create_constraint(client, [first["id"], second["id"]])
    await client.post(f"/api/v1/constraints/{constraint['id']}/retire", headers=ACTOR)

    response = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "constraint", "entity_id": constraint["id"]},
    )
    entries: list[dict[str, Any]] = response.json()["items"]
    assert [entry["action"] for entry in entries] == ["status_changed", "created"]

    retirement, creation = entries
    assert creation["before"] is None
    assert creation["after"]["kind"] == "mutual_exclusive_maintenance"
    assert creation["after"]["asset_ids"] == [first["id"], second["id"]]
    assert retirement["before"] == {"status": "active"}
    assert retirement["after"] == {"status": "retired"}


async def test_get_missing_constraint(client: AsyncClient) -> None:
    response = await client.get("/api/v1/constraints/missing-id")
    assert response.status_code == 404
    assert response.json()["error_code"] == "constraint.not_found"
