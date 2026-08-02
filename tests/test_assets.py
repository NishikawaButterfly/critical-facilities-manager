from __future__ import annotations

import pytest
from httpx import AsyncClient

from .conftest import ACTOR, create_asset, create_hierarchy

pytestmark = pytest.mark.anyio


async def test_create_asset_defaults_to_planned(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    assert asset["status"] == "planned"
    assert asset["criticality"] == "critical"
    assert asset["site_id"] == hierarchy["site"]["id"]


async def test_create_asset_with_initial_status(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"], status="operational")
    assert asset["status"] == "operational"


async def test_create_asset_at_building_level(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(
        client, hierarchy["building"]["id"], tag="GEN-C-01", asset_type="generator"
    )
    assert asset["location_id"] == hierarchy["building"]["id"]
    assert asset["site_id"] == hierarchy["site"]["id"]


async def test_create_asset_with_unknown_location(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/assets",
        json={
            "tag": "UPS-C-01",
            "name": "UPS System 1",
            "asset_type": "ups",
            "criticality": "critical",
            "location_id": "missing-id",
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "asset.location_not_found"


async def test_duplicate_tag_within_site_conflicts(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    await create_asset(client, hierarchy["room"]["id"])
    response = await client.post(
        "/api/v1/assets",
        json={
            "tag": "UPS-C-01",
            "name": "UPS System 2",
            "asset_type": "ups",
            "criticality": "high",
            "location_id": hierarchy["building"]["id"],
        },
        headers=ACTOR,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "asset.duplicate_tag"


async def test_same_tag_across_sites_is_allowed(client: AsyncClient) -> None:
    hierarchy_a = await create_hierarchy(client, "A")
    hierarchy_b = await create_hierarchy(client, "B")
    await create_asset(client, hierarchy_a["room"]["id"])
    asset = await create_asset(client, hierarchy_b["room"]["id"])
    assert asset["site_id"] == hierarchy_b["site"]["id"]


async def test_legal_transition_chain(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    for target in [
        "installed",
        "operational",
        "under_maintenance",
        "operational",
        "decommissioned",
    ]:
        response = await client.post(
            f"/api/v1/assets/{asset['id']}/transition",
            json={"to_status": target},
            headers=ACTOR,
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == target


@pytest.mark.parametrize(
    ("initial", "target"),
    [
        ("planned", "operational"),
        ("planned", "decommissioned"),
        ("installed", "under_maintenance"),
        ("operational", "planned"),
        ("decommissioned", "operational"),
    ],
)
async def test_illegal_transitions_rejected(client: AsyncClient, initial: str, target: str) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"], status=initial)
    response = await client.post(
        f"/api/v1/assets/{asset['id']}/transition",
        json={"to_status": target},
        headers=ACTOR,
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["error_code"] == "asset.invalid_transition"
    assert initial in payload["detail"]
    assert target in payload["detail"]

    unchanged = await client.get(f"/api/v1/assets/{asset['id']}")
    assert unchanged.json()["status"] == initial


async def test_self_transition_rejected(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"], status="operational")
    response = await client.post(
        f"/api/v1/assets/{asset['id']}/transition",
        json={"to_status": "operational"},
        headers=ACTOR,
    )
    assert response.status_code == 409


async def test_update_asset_fields(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    response = await client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"name": "UPS System 1B", "criticality": "high", "tag": "UPS-C-02"},
        headers=ACTOR,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "UPS System 1B"
    assert payload["criticality"] == "high"
    assert payload["tag"] == "UPS-C-02"


async def test_update_cannot_set_status(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    response = await client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"status": "operational"},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "api.validation_failed"


async def test_move_asset_to_other_site_recomputes_site_and_checks_tag(
    client: AsyncClient,
) -> None:
    hierarchy_a = await create_hierarchy(client, "A")
    hierarchy_b = await create_hierarchy(client, "B")
    asset = await create_asset(client, hierarchy_a["room"]["id"])
    await create_asset(client, hierarchy_b["room"]["id"])

    conflict = await client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"location_id": hierarchy_b["room"]["id"]},
        headers=ACTOR,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "asset.duplicate_tag"

    moved = await client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"location_id": hierarchy_b["room"]["id"], "tag": "UPS-C-99"},
        headers=ACTOR,
    )
    assert moved.status_code == 200
    assert moved.json()["site_id"] == hierarchy_b["site"]["id"]
    assert moved.json()["tag"] == "UPS-C-99"


async def test_get_missing_asset(client: AsyncClient) -> None:
    response = await client.get("/api/v1/assets/missing-id")
    assert response.status_code == 404
    assert response.json()["error_code"] == "asset.not_found"


async def test_list_assets_with_filters(client: AsyncClient) -> None:
    hierarchy_a = await create_hierarchy(client, "A")
    hierarchy_b = await create_hierarchy(client, "B")
    await create_asset(client, hierarchy_a["room"]["id"], status="operational")
    await create_asset(
        client,
        hierarchy_a["building"]["id"],
        tag="GEN-C-01",
        asset_type="generator",
        criticality="high",
    )
    await create_asset(client, hierarchy_b["room"]["id"], tag="UPS-C-02")

    by_site = await client.get("/api/v1/assets", params={"site_id": hierarchy_a["site"]["id"]})
    assert by_site.json()["total"] == 2

    by_status = await client.get("/api/v1/assets", params={"status": "operational"})
    assert by_status.json()["total"] == 1

    by_criticality = await client.get("/api/v1/assets", params={"criticality": "high"})
    assert by_criticality.json()["total"] == 1

    by_location = await client.get(
        "/api/v1/assets", params={"location_id": hierarchy_b["room"]["id"]}
    )
    assert by_location.json()["total"] == 1
    assert by_location.json()["items"][0]["tag"] == "UPS-C-02"
