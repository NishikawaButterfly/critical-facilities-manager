from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import (
    ACTOR,
    APPROVER,
    create_approved_procedure,
    create_asset,
    create_hierarchy,
    create_order,
    create_permit,
    create_procedure,
)

pytestmark = pytest.mark.anyio


async def _order(client: AsyncClient, *steps: str, **overrides: Any) -> dict[str, Any]:
    """Create an order against a fresh asset and walk it through the given steps."""
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"], status="operational")
    order = await create_order(client, asset["id"], **overrides)
    for step in steps:
        body = {"completion_notes": "Done."} if step == "complete" else None
        response = await client.post(
            f"/api/v1/maintenance-orders/{order['id']}/{step}", json=body, headers=ACTOR
        )
        assert response.status_code == 200, response.text
    refreshed = await client.get(f"/api/v1/maintenance-orders/{order['id']}")
    payload: dict[str, Any] = refreshed.json()
    return payload


async def test_create_permit_on_scheduled_order(client: AsyncClient) -> None:
    order = await _order(client, "schedule")
    permit = await create_permit(client, order["id"])
    assert permit["status"] == "requested"
    assert permit["order_id"] == order["id"]
    assert permit["procedure_id"] is None
    assert permit["requested_by"] == "test-engineer"
    assert permit["issued_by"] is None
    assert permit["completion_note"] is None


async def test_create_permit_on_in_progress_order(client: AsyncClient) -> None:
    order = await _order(client, "schedule", "start")
    permit = await create_permit(client, order["id"])
    assert permit["status"] == "requested"


@pytest.mark.parametrize(
    "steps",
    [[], ["schedule", "start", "complete"], ["cancel"]],
    ids=["draft", "done", "cancelled"],
)
async def test_create_permit_rejects_inactive_orders(client: AsyncClient, steps: list[str]) -> None:
    order = await _order(client, *steps)
    response = await client.post(
        "/api/v1/work-permits",
        json={"order_id": order["id"], "scope": "Any work at all."},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "work_permit.order_not_active"


async def test_create_permit_with_unknown_order(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/work-permits",
        json={"order_id": "missing-id", "scope": "Any work at all."},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "work_permit.order_not_found"


async def test_create_permit_with_approved_procedure(client: AsyncClient) -> None:
    order = await _order(client, "schedule")
    procedure = await create_approved_procedure(client)
    permit = await create_permit(client, order["id"], procedure_id=procedure["id"])
    assert permit["procedure_id"] == procedure["id"]


@pytest.mark.parametrize("prepare", ["draft", "retired"])
async def test_create_permit_rejects_unapproved_procedures(
    client: AsyncClient, prepare: str
) -> None:
    order = await _order(client, "schedule")
    if prepare == "draft":
        procedure = await create_procedure(client)
    else:
        procedure = await create_approved_procedure(client)
        await client.post(f"/api/v1/procedures/{procedure['id']}/retire", headers=ACTOR)
    response = await client.post(
        "/api/v1/work-permits",
        json={
            "order_id": order["id"],
            "procedure_id": procedure["id"],
            "scope": "Any work at all.",
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "work_permit.procedure_not_approved"


async def test_create_permit_with_unknown_procedure(client: AsyncClient) -> None:
    order = await _order(client, "schedule")
    response = await client.post(
        "/api/v1/work-permits",
        json={
            "order_id": order["id"],
            "procedure_id": "missing-id",
            "scope": "Any work at all.",
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "work_permit.procedure_not_found"


async def test_issue_requires_a_distinct_actor(client: AsyncClient) -> None:
    order = await _order(client, "schedule")
    permit = await create_permit(client, order["id"])

    rejected = await client.post(f"/api/v1/work-permits/{permit['id']}/issue", headers=ACTOR)
    assert rejected.status_code == 409
    assert rejected.json()["error_code"] == "work_permit.issuer_must_differ"

    issued = await client.post(f"/api/v1/work-permits/{permit['id']}/issue", headers=APPROVER)
    assert issued.status_code == 200
    assert issued.json()["status"] == "issued"
    assert issued.json()["issued_by"] == "test-approver"


async def test_close_requires_a_completion_note(client: AsyncClient) -> None:
    order = await _order(client, "schedule")
    permit = await create_permit(client, order["id"])
    await client.post(f"/api/v1/work-permits/{permit['id']}/issue", headers=APPROVER)

    missing_body = await client.post(f"/api/v1/work-permits/{permit['id']}/close", headers=ACTOR)
    assert missing_body.status_code == 422
    assert missing_body.json()["error_code"] == "api.validation_failed"

    blank_note = await client.post(
        f"/api/v1/work-permits/{permit['id']}/close",
        json={"completion_note": "   "},
        headers=ACTOR,
    )
    assert blank_note.status_code == 422
    assert blank_note.json()["error_code"] == "work_permit.completion_note_required"

    closed = await client.post(
        f"/api/v1/work-permits/{permit['id']}/close",
        json={"completion_note": "Work verified complete; area returned to service."},
        headers=ACTOR,
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert closed.json()["completion_note"] == "Work verified complete; area returned to service."


async def test_revoke_with_an_optional_reason(client: AsyncClient) -> None:
    order = await _order(client, "schedule")
    permit = await create_permit(client, order["id"])
    await client.post(f"/api/v1/work-permits/{permit['id']}/issue", headers=APPROVER)

    revoked = await client.post(
        f"/api/v1/work-permits/{permit['id']}/revoke",
        json={"reason": "Fire alarm test scheduled in the same room."},
        headers=APPROVER,
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["completion_note"] == "Fire alarm test scheduled in the same room."


@pytest.mark.parametrize(
    ("action", "prepare"),
    [
        ("close", []),
        ("revoke", []),
        ("issue", ["issue"]),
        ("issue", ["issue", "close"]),
        ("close", ["issue", "close"]),
        ("revoke", ["issue", "revoke"]),
    ],
)
async def test_illegal_permit_transitions_rejected(
    client: AsyncClient, action: str, prepare: list[str]
) -> None:
    order = await _order(client, "schedule")
    permit = await create_permit(client, order["id"])
    for step in prepare:
        body = {"completion_note": "Done."} if step == "close" else None
        response = await client.post(
            f"/api/v1/work-permits/{permit['id']}/{step}", json=body, headers=APPROVER
        )
        assert response.status_code == 200, response.text

    body = {"completion_note": "Done."} if action == "close" else None
    response = await client.post(
        f"/api/v1/work-permits/{permit['id']}/{action}", json=body, headers=APPROVER
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "work_permit.invalid_transition"


async def test_order_cannot_complete_with_an_issued_permit(client: AsyncClient) -> None:
    order = await _order(client, "schedule")
    permit = await create_permit(client, order["id"])
    await client.post(f"/api/v1/work-permits/{permit['id']}/issue", headers=APPROVER)
    await client.post(f"/api/v1/maintenance-orders/{order['id']}/start", headers=ACTOR)

    blocked = await client.post(
        f"/api/v1/maintenance-orders/{order['id']}/complete",
        json={"completion_notes": "All parameters nominal."},
        headers=ACTOR,
    )
    assert blocked.status_code == 409
    assert blocked.json()["error_code"] == "maintenance_order.open_permit"

    still_in_progress = await client.get(f"/api/v1/maintenance-orders/{order['id']}")
    assert still_in_progress.json()["status"] == "in_progress"

    await client.post(
        f"/api/v1/work-permits/{permit['id']}/close",
        json={"completion_note": "Work verified complete."},
        headers=ACTOR,
    )
    completed = await client.post(
        f"/api/v1/maintenance-orders/{order['id']}/complete",
        json={"completion_notes": "All parameters nominal."},
        headers=ACTOR,
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "done"


async def test_revoked_permit_unblocks_completion(client: AsyncClient) -> None:
    order = await _order(client, "schedule", "start")
    permit = await create_permit(client, order["id"])
    await client.post(f"/api/v1/work-permits/{permit['id']}/issue", headers=APPROVER)
    await client.post(f"/api/v1/work-permits/{permit['id']}/revoke", headers=APPROVER)

    completed = await client.post(
        f"/api/v1/maintenance-orders/{order['id']}/complete",
        json={"completion_notes": "All parameters nominal."},
        headers=ACTOR,
    )
    assert completed.status_code == 200


async def test_requested_permit_does_not_block_completion(client: AsyncClient) -> None:
    order = await _order(client, "schedule", "start")
    await create_permit(client, order["id"])

    completed = await client.post(
        f"/api/v1/maintenance-orders/{order['id']}/complete",
        json={"completion_notes": "All parameters nominal."},
        headers=ACTOR,
    )
    assert completed.status_code == 200


async def test_list_permits_with_filters(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"], status="operational")
    order_1 = await create_order(client, asset["id"])
    order_2 = await create_order(client, asset["id"], title="Second inspection")
    for order in (order_1, order_2):
        await client.post(f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR)
    await create_permit(client, order_1["id"])
    permit_2 = await create_permit(client, order_2["id"])
    await client.post(f"/api/v1/work-permits/{permit_2['id']}/issue", headers=APPROVER)

    by_order = await client.get("/api/v1/work-permits", params={"order_id": order_1["id"]})
    assert by_order.json()["total"] == 1
    assert by_order.json()["items"][0]["status"] == "requested"

    by_status = await client.get("/api/v1/work-permits", params={"status": "issued"})
    assert by_status.json()["total"] == 1
    assert by_status.json()["items"][0]["id"] == permit_2["id"]


async def test_get_missing_permit(client: AsyncClient) -> None:
    response = await client.get("/api/v1/work-permits/missing-id")
    assert response.status_code == 404
    assert response.json()["error_code"] == "work_permit.not_found"


async def test_permit_audit_trail(client: AsyncClient) -> None:
    order = await _order(client, "schedule")
    permit = await create_permit(client, order["id"])
    await client.post(f"/api/v1/work-permits/{permit['id']}/issue", headers=APPROVER)
    await client.post(
        f"/api/v1/work-permits/{permit['id']}/close",
        json={"completion_note": "Work verified complete."},
        headers=ACTOR,
    )

    response = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "work_permit", "entity_id": permit["id"]},
    )
    entries: list[dict[str, Any]] = response.json()["items"]
    assert [entry["action"] for entry in entries] == ["status_changed", "status_changed", "created"]

    closure, issuance, creation = entries
    assert creation["before"] is None
    assert creation["after"]["status"] == "requested"
    assert creation["after"]["requested_by"] == "test-engineer"

    assert issuance["actor"] == "test-approver"
    assert issuance["before"] == {"status": "requested", "issued_by": None}
    assert issuance["after"] == {"status": "issued", "issued_by": "test-approver"}

    assert closure["before"] == {"status": "issued", "completion_note": None}
    assert closure["after"] == {
        "status": "closed",
        "completion_note": "Work verified complete.",
    }
