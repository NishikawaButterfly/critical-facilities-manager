from __future__ import annotations

import pytest
from httpx import AsyncClient

from .conftest import ACTOR, create_asset, create_hierarchy, create_location

pytestmark = pytest.mark.anyio


async def test_full_hierarchy_creation(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    assert hierarchy["site"]["kind"] == "site"
    assert hierarchy["site"]["parent_id"] is None
    assert hierarchy["building"]["parent_id"] == hierarchy["site"]["id"]
    assert hierarchy["floor"]["parent_id"] == hierarchy["building"]["id"]
    assert hierarchy["room"]["parent_id"] == hierarchy["floor"]["id"]


async def test_site_must_not_have_parent(client: AsyncClient) -> None:
    site = await create_location(client, "site", "CAMP-A", "Campus A")
    response = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-B", "name": "Campus B", "parent_id": site["id"]},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "location.invalid_parent_kind"


async def test_non_site_requires_parent(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/locations",
        json={"kind": "building", "code": "BLDG-C", "name": "Building C"},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "location.parent_required"


@pytest.mark.parametrize(
    ("child_kind", "parent_key"),
    [
        ("room", "building"),
        ("room", "site"),
        ("floor", "site"),
        ("floor", "room"),
        ("building", "floor"),
    ],
)
async def test_adjacency_rule_rejects_skipped_levels(
    client: AsyncClient, child_kind: str, parent_key: str
) -> None:
    hierarchy = await create_hierarchy(client)
    response = await client.post(
        "/api/v1/locations",
        json={
            "kind": child_kind,
            "code": "X-1",
            "name": "Wrongly placed",
            "parent_id": hierarchy[parent_key]["id"],
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "location.invalid_parent_kind"


async def test_parent_not_found(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/locations",
        json={
            "kind": "building",
            "code": "BLDG-C",
            "name": "Building C",
            "parent_id": "missing-id",
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "location.parent_not_found"


async def test_duplicate_code_under_same_parent_conflicts(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    response = await client.post(
        "/api/v1/locations",
        json={
            "kind": "floor",
            "code": "L1",
            "name": "Level 1 again",
            "parent_id": hierarchy["building"]["id"],
        },
        headers=ACTOR,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "location.duplicate_code"


async def test_duplicate_site_code_conflicts(client: AsyncClient) -> None:
    await create_location(client, "site", "CAMP-A", "Campus A")
    response = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-A", "name": "Campus A again"},
        headers=ACTOR,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "location.duplicate_code"


async def test_same_code_under_different_parents_is_allowed(client: AsyncClient) -> None:
    site = await create_location(client, "site", "CAMP-A", "Campus A")
    building_1 = await create_location(client, "building", "BLDG-B", "Building B", site["id"])
    building_2 = await create_location(client, "building", "BLDG-C", "Building C", site["id"])
    await create_location(client, "floor", "L1", "Level 1", building_1["id"])
    floor = await create_location(client, "floor", "L1", "Level 1", building_2["id"])
    assert floor["code"] == "L1"


async def test_get_location_and_missing_location(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    response = await client.get(f"/api/v1/locations/{hierarchy['room']['id']}")
    assert response.status_code == 200
    assert response.json()["code"] == "L1-DH1"

    missing = await client.get("/api/v1/locations/missing-id")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "location.not_found"


async def test_list_locations_with_filters(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    rooms = await client.get("/api/v1/locations", params={"kind": "room"})
    assert rooms.json()["total"] == 1
    assert rooms.json()["items"][0]["id"] == hierarchy["room"]["id"]

    children = await client.get(
        "/api/v1/locations", params={"parent_id": hierarchy["building"]["id"]}
    )
    assert children.json()["total"] == 1
    assert children.json()["items"][0]["kind"] == "floor"


async def test_update_location_code_and_name(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    room_id = hierarchy["room"]["id"]
    response = await client.patch(
        f"/api/v1/locations/{room_id}",
        json={"code": "L1-DH2", "name": "Data Hall 2"},
        headers=ACTOR,
    )
    assert response.status_code == 200
    assert response.json()["code"] == "L1-DH2"
    assert response.json()["name"] == "Data Hall 2"


async def test_update_to_duplicate_code_conflicts(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    other_room = await create_location(
        client, "room", "L1-DH2", "Data Hall 2", hierarchy["floor"]["id"]
    )
    response = await client.patch(
        f"/api/v1/locations/{other_room['id']}",
        json={"code": "L1-DH1"},
        headers=ACTOR,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "location.duplicate_code"


async def test_update_rejects_unknown_fields(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    response = await client.patch(
        f"/api/v1/locations/{hierarchy['room']['id']}",
        json={"kind": "site"},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "api.validation_failed"


async def test_delete_location_with_children_conflicts(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    response = await client.delete(f"/api/v1/locations/{hierarchy['floor']['id']}", headers=ACTOR)
    assert response.status_code == 409
    assert response.json()["error_code"] == "location.has_children"


async def test_delete_location_with_assets_conflicts(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    await create_asset(client, hierarchy["room"]["id"])
    response = await client.delete(f"/api/v1/locations/{hierarchy['room']['id']}", headers=ACTOR)
    assert response.status_code == 409
    assert response.json()["error_code"] == "location.has_assets"


async def test_delete_empty_location(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    room_id = hierarchy["room"]["id"]
    response = await client.delete(f"/api/v1/locations/{room_id}", headers=ACTOR)
    assert response.status_code == 204
    missing = await client.get(f"/api/v1/locations/{room_id}")
    assert missing.status_code == 404
