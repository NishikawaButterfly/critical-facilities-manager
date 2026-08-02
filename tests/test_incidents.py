from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import ACTOR, create_asset, create_hierarchy, create_incident

pytestmark = pytest.mark.anyio


async def _asset(client: AsyncClient) -> dict[str, Any]:
    hierarchy = await create_hierarchy(client)
    return await create_asset(client, hierarchy["room"]["id"], status="operational")


async def test_create_incident_starts_open(client: AsyncClient) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"], description="Tripped twice today.")
    assert incident["status"] == "open"
    assert incident["severity"] == "high"
    assert incident["asset_id"] == asset["id"]
    assert incident["description"] == "Tripped twice today."
    assert incident["resolution_notes"] is None
    assert incident["corrective_order_id"] is None


async def test_create_incident_with_unknown_asset(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/incidents",
        json={"asset_id": "missing-id", "severity": "low", "title": "Ghost reading"},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "incident.asset_not_found"


async def test_full_lifecycle_to_resolved(client: AsyncClient) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"])
    incident_id = incident["id"]

    investigating = await client.post(
        f"/api/v1/incidents/{incident_id}/start-investigation", headers=ACTOR
    )
    assert investigating.status_code == 200
    assert investigating.json()["status"] == "investigating"

    resolved = await client.post(
        f"/api/v1/incidents/{incident_id}/resolve",
        json={"resolution_notes": "Faulty pressure transducer replaced."},
        headers=ACTOR,
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolution_notes"] == "Faulty pressure transducer replaced."


async def test_resolve_requires_notes(client: AsyncClient) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"])
    incident_id = incident["id"]
    await client.post(f"/api/v1/incidents/{incident_id}/start-investigation", headers=ACTOR)

    missing_body = await client.post(f"/api/v1/incidents/{incident_id}/resolve", headers=ACTOR)
    assert missing_body.status_code == 422
    assert missing_body.json()["error_code"] == "api.validation_failed"

    blank_notes = await client.post(
        f"/api/v1/incidents/{incident_id}/resolve",
        json={"resolution_notes": "   "},
        headers=ACTOR,
    )
    assert blank_notes.status_code == 422
    assert blank_notes.json()["error_code"] == "incident.resolution_notes_required"

    unchanged = await client.get(f"/api/v1/incidents/{incident_id}")
    assert unchanged.json()["status"] == "investigating"


@pytest.mark.parametrize("action", ["resolve", "dismiss"])
async def test_open_incident_must_be_investigated_first(client: AsyncClient, action: str) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"])

    body = {"resolution_notes": "Done."} if action == "resolve" else None
    response = await client.post(
        f"/api/v1/incidents/{incident['id']}/{action}", json=body, headers=ACTOR
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "incident.invalid_transition"


async def test_dismiss_with_an_optional_reason(client: AsyncClient) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"])
    incident_id = incident["id"]
    await client.post(f"/api/v1/incidents/{incident_id}/start-investigation", headers=ACTOR)

    dismissed = await client.post(
        f"/api/v1/incidents/{incident_id}/dismiss",
        json={"reason": "Sensor artifact; readings normal on inspection."},
        headers=ACTOR,
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "dismissed"
    assert dismissed.json()["resolution_notes"] == "Sensor artifact; readings normal on inspection."


async def test_terminal_incident_rejects_all_transitions(client: AsyncClient) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"])
    incident_id = incident["id"]
    await client.post(f"/api/v1/incidents/{incident_id}/start-investigation", headers=ACTOR)
    await client.post(
        f"/api/v1/incidents/{incident_id}/resolve",
        json={"resolution_notes": "Root cause fixed."},
        headers=ACTOR,
    )

    for action, body in [
        ("start-investigation", None),
        ("resolve", {"resolution_notes": "Again."}),
        ("dismiss", None),
    ]:
        response = await client.post(
            f"/api/v1/incidents/{incident_id}/{action}", json=body, headers=ACTOR
        )
        assert response.status_code == 409
        assert response.json()["error_code"] == "incident.invalid_transition"


async def test_corrective_order_is_prefilled_from_the_incident(client: AsyncClient) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"], description="Tripped twice today.")

    response = await client.post(
        f"/api/v1/incidents/{incident['id']}/corrective-order",
        json={"due_date": "2026-09-15"},
        headers=ACTOR,
    )
    assert response.status_code == 201
    order = response.json()
    assert order["order_type"] == "corrective"
    assert order["status"] == "draft"
    assert order["asset_id"] == asset["id"]
    assert order["title"] == incident["title"]
    assert order["description"] == "Tripped twice today."
    assert order["due_date"] == "2026-09-15"

    linked = await client.get(f"/api/v1/incidents/{incident['id']}")
    assert linked.json()["corrective_order_id"] == order["id"]


async def test_corrective_order_accepts_overrides(client: AsyncClient) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"])

    response = await client.post(
        f"/api/v1/incidents/{incident['id']}/corrective-order",
        json={
            "due_date": "2026-09-15",
            "title": "Replace the discharge pressure sensor",
            "description": "Sensor drifted out of calibration.",
        },
        headers=ACTOR,
    )
    assert response.status_code == 201
    assert response.json()["title"] == "Replace the discharge pressure sensor"
    assert response.json()["description"] == "Sensor drifted out of calibration."


async def test_corrective_order_only_once_per_incident(client: AsyncClient) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"])
    first = await client.post(
        f"/api/v1/incidents/{incident['id']}/corrective-order",
        json={"due_date": "2026-09-15"},
        headers=ACTOR,
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/v1/incidents/{incident['id']}/corrective-order",
        json={"due_date": "2026-09-20"},
        headers=ACTOR,
    )
    assert second.status_code == 409
    assert second.json()["error_code"] == "incident.corrective_order_exists"


@pytest.mark.parametrize("outcome", ["resolved", "dismissed"])
async def test_corrective_order_requires_an_active_incident(
    client: AsyncClient, outcome: str
) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"])
    incident_id = incident["id"]
    await client.post(f"/api/v1/incidents/{incident_id}/start-investigation", headers=ACTOR)
    if outcome == "resolved":
        await client.post(
            f"/api/v1/incidents/{incident_id}/resolve",
            json={"resolution_notes": "Fixed."},
            headers=ACTOR,
        )
    else:
        await client.post(f"/api/v1/incidents/{incident_id}/dismiss", headers=ACTOR)

    response = await client.post(
        f"/api/v1/incidents/{incident_id}/corrective-order",
        json={"due_date": "2026-09-15"},
        headers=ACTOR,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "incident.not_active"


async def test_corrective_order_audit_links_both_ways(client: AsyncClient) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"])
    response = await client.post(
        f"/api/v1/incidents/{incident['id']}/corrective-order",
        json={"due_date": "2026-09-15"},
        headers=ACTOR,
    )
    order_id = response.json()["id"]

    incident_entries = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "incident", "entity_id": incident["id"]},
    )
    link_entry = incident_entries.json()["items"][0]
    assert link_entry["action"] == "corrective_order_created"
    assert link_entry["before"] == {"corrective_order_id": None}
    assert link_entry["after"] == {"corrective_order_id": order_id}

    order_entries = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "maintenance_order", "entity_id": order_id},
    )
    actions = [entry["action"] for entry in order_entries.json()["items"]]
    assert actions == ["created_from_incident", "created"]
    back_link = order_entries.json()["items"][0]
    assert back_link["after"] == {"incident_id": incident["id"]}


async def test_incident_audit_trail(client: AsyncClient) -> None:
    asset = await _asset(client)
    incident = await create_incident(client, asset["id"])
    incident_id = incident["id"]
    await client.post(f"/api/v1/incidents/{incident_id}/start-investigation", headers=ACTOR)
    await client.post(
        f"/api/v1/incidents/{incident_id}/resolve",
        json={"resolution_notes": "Root cause fixed."},
        headers=ACTOR,
    )

    response = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "incident", "entity_id": incident_id},
    )
    entries: list[dict[str, Any]] = response.json()["items"]
    assert [entry["action"] for entry in entries] == ["status_changed", "status_changed", "created"]

    resolution, investigation, creation = entries
    assert creation["before"] is None
    assert creation["after"]["status"] == "open"
    assert creation["after"]["severity"] == "high"

    assert investigation["before"] == {"status": "open"}
    assert investigation["after"] == {"status": "investigating"}

    assert resolution["before"] == {"status": "investigating", "resolution_notes": None}
    assert resolution["after"] == {
        "status": "resolved",
        "resolution_notes": "Root cause fixed.",
    }


async def test_list_incidents_with_filters(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset_1 = await create_asset(client, hierarchy["room"]["id"], status="operational")
    asset_2 = await create_asset(
        client, hierarchy["room"]["id"], tag="CRAC-C-01", asset_type="crac"
    )
    await create_incident(client, asset_1["id"], severity="high")
    incident = await create_incident(client, asset_2["id"], severity="low", title="Odd noise")
    await client.post(f"/api/v1/incidents/{incident['id']}/start-investigation", headers=ACTOR)

    by_asset = await client.get("/api/v1/incidents", params={"asset_id": asset_1["id"]})
    assert by_asset.json()["total"] == 1

    by_severity = await client.get("/api/v1/incidents", params={"severity": "low"})
    assert by_severity.json()["total"] == 1
    assert by_severity.json()["items"][0]["title"] == "Odd noise"

    by_status = await client.get("/api/v1/incidents", params={"status": "investigating"})
    assert by_status.json()["total"] == 1

    combined = await client.get(
        "/api/v1/incidents", params={"severity": "high", "status": "investigating"}
    )
    assert combined.json()["total"] == 0


async def test_get_missing_incident(client: AsyncClient) -> None:
    response = await client.get("/api/v1/incidents/missing-id")
    assert response.status_code == 404
    assert response.json()["error_code"] == "incident.not_found"
