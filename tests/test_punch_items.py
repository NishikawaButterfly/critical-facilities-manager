from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import ACTOR, APPROVER, create_asset, create_hierarchy, create_punch_item

pytestmark = pytest.mark.anyio


async def test_create_asset_scoped_item(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    item = await create_punch_item(
        client,
        asset_id=asset["id"],
        description="Guard panel was never refitted after installation.",
        due_date="2026-09-01",
    )
    assert item["status"] == "open"
    assert item["asset_id"] == asset["id"]
    assert item["location_id"] is None
    assert item["category"] == "defect"
    assert item["severity"] == "major"
    assert item["due_date"] == "2026-09-01"
    assert item["started_by"] is None
    assert item["verified_by"] is None
    assert item["closing_note"] is None


async def test_create_location_scoped_item(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    item = await create_punch_item(
        client,
        location_id=hierarchy["room"]["id"],
        category="documentation",
        severity="blocking",
        title="As-built drawings missing",
    )
    assert item["asset_id"] is None
    assert item["location_id"] == hierarchy["room"]["id"]
    assert item["due_date"] is None


@pytest.mark.parametrize("scope", ["both", "neither"])
async def test_scope_must_be_exactly_one(client: AsyncClient, scope: str) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    body: dict[str, Any] = {
        "category": "defect",
        "severity": "minor",
        "title": "Loose cable tray cover",
    }
    if scope == "both":
        body["asset_id"] = asset["id"]
        body["location_id"] = hierarchy["room"]["id"]

    response = await client.post("/api/v1/punch-items", json=body, headers=ACTOR)
    assert response.status_code == 422
    assert response.json()["error_code"] == "punch_item.exactly_one_scope"


async def test_create_with_unknown_asset(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/punch-items",
        json={
            "asset_id": "missing-id",
            "category": "defect",
            "severity": "minor",
            "title": "Ghost finding",
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "punch_item.asset_not_found"


async def test_create_with_unknown_location(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/punch-items",
        json={
            "location_id": "missing-id",
            "category": "defect",
            "severity": "minor",
            "title": "Ghost finding",
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "punch_item.location_not_found"


async def _open_item(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    return await create_punch_item(client, asset_id=asset["id"], **overrides)


async def test_full_lifecycle_to_closed(client: AsyncClient) -> None:
    item = await _open_item(client)
    item_id = item["id"]

    started = await client.post(f"/api/v1/punch-items/{item_id}/start", headers=ACTOR)
    assert started.status_code == 200
    assert started.json()["status"] == "in_progress"
    assert started.json()["started_by"] == "test-engineer"

    closed = await client.post(
        f"/api/v1/punch-items/{item_id}/close",
        json={"closing_note": "Guard refitted and torque checked."},
        headers=APPROVER,
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert closed.json()["verified_by"] == "test-approver"
    assert closed.json()["closing_note"] == "Guard refitted and torque checked."


async def test_close_requires_a_note(client: AsyncClient) -> None:
    item = await _open_item(client)
    item_id = item["id"]
    await client.post(f"/api/v1/punch-items/{item_id}/start", headers=ACTOR)

    missing_body = await client.post(f"/api/v1/punch-items/{item_id}/close", headers=APPROVER)
    assert missing_body.status_code == 422
    assert missing_body.json()["error_code"] == "api.validation_failed"

    blank = await client.post(
        f"/api/v1/punch-items/{item_id}/close",
        json={"closing_note": "   "},
        headers=APPROVER,
    )
    assert blank.status_code == 422
    assert blank.json()["error_code"] == "punch_item.closing_note_required"

    unchanged = await client.get(f"/api/v1/punch-items/{item_id}")
    assert unchanged.json()["status"] == "in_progress"


async def test_verifier_must_differ_from_starter(client: AsyncClient) -> None:
    item = await _open_item(client)
    item_id = item["id"]
    await client.post(f"/api/v1/punch-items/{item_id}/start", headers=ACTOR)

    response = await client.post(
        f"/api/v1/punch-items/{item_id}/close",
        json={"closing_note": "Fixed it myself."},
        headers=ACTOR,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "punch_item.verifier_must_differ"

    unchanged = await client.get(f"/api/v1/punch-items/{item_id}")
    assert unchanged.json()["status"] == "in_progress"
    assert unchanged.json()["closing_note"] is None


async def test_close_from_open_rejected(client: AsyncClient) -> None:
    item = await _open_item(client)

    response = await client.post(
        f"/api/v1/punch-items/{item['id']}/close",
        json={"closing_note": "Closing without starting."},
        headers=APPROVER,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "punch_item.invalid_transition"


async def test_start_twice_rejected(client: AsyncClient) -> None:
    item = await _open_item(client)
    await client.post(f"/api/v1/punch-items/{item['id']}/start", headers=ACTOR)

    response = await client.post(f"/api/v1/punch-items/{item['id']}/start", headers=APPROVER)
    assert response.status_code == 409
    assert response.json()["error_code"] == "punch_item.invalid_transition"


async def test_closed_item_is_terminal(client: AsyncClient) -> None:
    item = await _open_item(client)
    item_id = item["id"]
    await client.post(f"/api/v1/punch-items/{item_id}/start", headers=ACTOR)
    await client.post(
        f"/api/v1/punch-items/{item_id}/close",
        json={"closing_note": "Verified complete."},
        headers=APPROVER,
    )

    for action, body in [("start", None), ("close", {"closing_note": "Again."})]:
        response = await client.post(
            f"/api/v1/punch-items/{item_id}/{action}", json=body, headers=APPROVER
        )
        assert response.status_code == 409
        assert response.json()["error_code"] == "punch_item.invalid_transition"


async def test_list_filters_by_scope(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    await create_punch_item(client, asset_id=asset["id"])
    await create_punch_item(client, location_id=hierarchy["room"]["id"], title="Room finding")

    by_asset = await client.get("/api/v1/punch-items", params={"asset_id": asset["id"]})
    assert by_asset.json()["total"] == 1
    assert by_asset.json()["items"][0]["asset_id"] == asset["id"]

    by_location = await client.get(
        "/api/v1/punch-items", params={"location_id": hierarchy["room"]["id"]}
    )
    assert by_location.json()["total"] == 1
    assert by_location.json()["items"][0]["title"] == "Room finding"


async def test_list_filters_by_category_severity_and_state(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    await create_punch_item(client, asset_id=asset["id"], category="safety", severity="blocking")
    started = await create_punch_item(
        client, asset_id=asset["id"], category="missing", severity="minor", title="Label missing"
    )
    await client.post(f"/api/v1/punch-items/{started['id']}/start", headers=ACTOR)

    by_category = await client.get("/api/v1/punch-items", params={"category": "safety"})
    assert by_category.json()["total"] == 1

    by_severity = await client.get("/api/v1/punch-items", params={"severity": "minor"})
    assert by_severity.json()["total"] == 1
    assert by_severity.json()["items"][0]["title"] == "Label missing"

    by_state = await client.get("/api/v1/punch-items", params={"status": "in_progress"})
    assert by_state.json()["total"] == 1

    combined = await client.get(
        "/api/v1/punch-items", params={"category": "safety", "status": "in_progress"}
    )
    assert combined.json()["total"] == 0


async def test_overdue_filter(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    overdue_item = await create_punch_item(
        client, asset_id=asset["id"], title="Overdue and open", due_date="2020-01-01"
    )
    await create_punch_item(
        client, asset_id=asset["id"], title="Due far in the future", due_date="2999-01-01"
    )
    await create_punch_item(client, asset_id=asset["id"], title="No due date")
    closed_late = await create_punch_item(
        client, asset_id=asset["id"], title="Closed after the due date", due_date="2020-01-01"
    )
    await client.post(f"/api/v1/punch-items/{closed_late['id']}/start", headers=ACTOR)
    await client.post(
        f"/api/v1/punch-items/{closed_late['id']}/close",
        json={"closing_note": "Done, late."},
        headers=APPROVER,
    )

    overdue = await client.get("/api/v1/punch-items", params={"overdue": "true"})
    assert overdue.json()["total"] == 1
    assert overdue.json()["items"][0]["id"] == overdue_item["id"]

    not_overdue = await client.get("/api/v1/punch-items", params={"overdue": "false"})
    assert not_overdue.json()["total"] == 3
    titles = {item["title"] for item in not_overdue.json()["items"]}
    assert titles == {"Due far in the future", "No due date", "Closed after the due date"}


async def test_punch_item_audit_trail(client: AsyncClient) -> None:
    item = await _open_item(client)
    item_id = item["id"]
    await client.post(f"/api/v1/punch-items/{item_id}/start", headers=ACTOR)
    await client.post(
        f"/api/v1/punch-items/{item_id}/close",
        json={"closing_note": "Verified on site."},
        headers=APPROVER,
    )

    response = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "punch_item", "entity_id": item_id},
    )
    entries: list[dict[str, Any]] = response.json()["items"]
    assert [entry["action"] for entry in entries] == ["status_changed", "status_changed", "created"]

    closing, starting, creation = entries
    assert creation["before"] is None
    assert creation["after"]["status"] == "open"
    assert creation["after"]["severity"] == "major"

    assert starting["actor"] == "test-engineer"
    assert starting["before"] == {"status": "open", "started_by": None}
    assert starting["after"] == {"status": "in_progress", "started_by": "test-engineer"}

    assert closing["actor"] == "test-approver"
    assert closing["before"] == {
        "status": "in_progress",
        "verified_by": None,
        "closing_note": None,
    }
    assert closing["after"] == {
        "status": "closed",
        "verified_by": "test-approver",
        "closing_note": "Verified on site.",
    }


async def test_get_missing_item(client: AsyncClient) -> None:
    response = await client.get("/api/v1/punch-items/missing-id")
    assert response.status_code == 404
    assert response.json()["error_code"] == "punch_item.not_found"
