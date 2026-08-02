from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import (
    ACTOR,
    ADMIN,
    create_approved_procedure,
    create_asset,
    create_commissioning_test,
    create_hierarchy,
    create_procedure,
)

pytestmark = pytest.mark.anyio

WITNESS = "test-witness"


async def _asset(client: AsyncClient) -> dict[str, Any]:
    hierarchy = await create_hierarchy(client)
    return await create_asset(client, hierarchy["room"]["id"], status="installed")


async def test_create_test_starts_pending(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])
    assert test["status"] == "pending"
    assert test["asset_id"] == asset["id"]
    assert test["procedure_id"] is None
    assert test["planned_date"] == "2026-08-20"
    assert test["executed_by"] is None
    assert test["witnessed_by"] is None
    assert test["punch_item_id"] is None
    assert test["evidence"] == []


async def test_create_test_with_unknown_asset(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/commissioning-tests",
        json={"asset_id": "missing-id", "title": "Load test", "planned_date": "2026-08-20"},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "commissioning_test.asset_not_found"


async def test_create_test_with_approved_procedure(client: AsyncClient) -> None:
    asset = await _asset(client)
    procedure = await create_approved_procedure(client)
    test = await create_commissioning_test(client, asset["id"], procedure_id=procedure["id"])
    assert test["procedure_id"] == procedure["id"]


async def test_create_test_with_unknown_procedure(client: AsyncClient) -> None:
    asset = await _asset(client)
    response = await client.post(
        "/api/v1/commissioning-tests",
        json={
            "asset_id": asset["id"],
            "procedure_id": "missing-id",
            "title": "Load test",
            "planned_date": "2026-08-20",
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "commissioning_test.procedure_not_found"


@pytest.mark.parametrize("procedure_status", ["draft", "retired"])
async def test_create_test_with_unapproved_procedure(
    client: AsyncClient, procedure_status: str
) -> None:
    asset = await _asset(client)
    if procedure_status == "draft":
        procedure = await create_procedure(client)
    else:
        procedure = await create_approved_procedure(client)
        retired = await client.post(f"/api/v1/procedures/{procedure['id']}/retire", headers=ACTOR)
        assert retired.status_code == 200

    response = await client.post(
        "/api/v1/commissioning-tests",
        json={
            "asset_id": asset["id"],
            "procedure_id": procedure["id"],
            "title": "Load test",
            "planned_date": "2026-08-20",
        },
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "commissioning_test.procedure_not_approved"


async def test_append_evidence_while_pending(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])

    first = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/evidence",
        json={"note": "Battery string voltages within tolerance."},
        headers=ACTOR,
    )
    assert first.status_code == 201
    second = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/evidence",
        json={"note": "Transfer to bypass completed in under 10 ms."},
        headers=ACTOR,
    )
    assert second.status_code == 201

    evidence = second.json()["evidence"]
    assert [entry["note"] for entry in evidence] == [
        "Battery string voltages within tolerance.",
        "Transfer to bypass completed in under 10 ms.",
    ]
    assert all(entry["actor"] == "test-engineer" for entry in evidence)
    assert all(entry["recorded_at"] is not None for entry in evidence)


async def test_blank_evidence_note_rejected(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])

    missing_body = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/evidence", headers=ACTOR
    )
    assert missing_body.status_code == 422
    assert missing_body.json()["error_code"] == "api.validation_failed"

    blank = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/evidence",
        json={"note": "   "},
        headers=ACTOR,
    )
    assert blank.status_code == 422
    assert blank.json()["error_code"] == "commissioning_test.evidence_note_required"


async def test_pass_records_executor_and_witness(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])

    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/pass",
        json={"witness": WITNESS},
        headers=ACTOR,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "passed"
    assert payload["executed_by"] == "test-engineer"
    assert payload["witnessed_by"] == WITNESS


async def test_record_appends_a_final_evidence_note(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])

    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/fail",
        json={"witness": WITNESS, "evidence_note": "Alarm relay never picked up."},
        headers=ACTOR,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert [entry["note"] for entry in payload["evidence"]] == ["Alarm relay never picked up."]


async def test_witness_must_differ_from_executor(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])

    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/pass",
        json={"witness": "test-engineer"},
        headers=ACTOR,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "commissioning_test.witness_must_differ"

    unchanged = await client.get(f"/api/v1/commissioning-tests/{test['id']}")
    assert unchanged.json()["status"] == "pending"


async def test_unknown_witness_rejected(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])

    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/pass",
        json={"witness": "nobody-here"},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "commissioning_test.witness_not_active_user"

    unchanged = await client.get(f"/api/v1/commissioning-tests/{test['id']}")
    assert unchanged.json()["status"] == "pending"


async def test_inactive_witness_rejected(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])

    users = await client.get("/api/v1/users", headers=ADMIN)
    witness_user = next(item for item in users.json()["items"] if item["username"] == WITNESS)
    deactivated = await client.post(f"/api/v1/users/{witness_user['id']}/deactivate", headers=ADMIN)
    assert deactivated.status_code == 200

    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/pass",
        json={"witness": WITNESS},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "commissioning_test.witness_not_active_user"


async def test_blank_witness_rejected(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])

    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/pass",
        json={"witness": "   "},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "commissioning_test.witness_required"


@pytest.mark.parametrize("first_outcome", ["pass", "fail"])
async def test_recorded_test_is_terminal(client: AsyncClient, first_outcome: str) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])
    recorded = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/{first_outcome}",
        json={"witness": WITNESS},
        headers=ACTOR,
    )
    assert recorded.status_code == 200

    for action in ["pass", "fail"]:
        response = await client.post(
            f"/api/v1/commissioning-tests/{test['id']}/{action}",
            json={"witness": WITNESS},
            headers=ACTOR,
        )
        assert response.status_code == 409
        assert response.json()["error_code"] == "commissioning_test.invalid_transition"


async def test_evidence_rejected_after_recording(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])
    await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/pass",
        json={"witness": WITNESS},
        headers=ACTOR,
    )

    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/evidence",
        json={"note": "Late note."},
        headers=ACTOR,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "commissioning_test.not_pending"


async def _failed_test(client: AsyncClient) -> dict[str, Any]:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])
    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/fail",
        json={"witness": WITNESS},
        headers=ACTOR,
    )
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def test_punch_item_prefilled_from_failed_test(client: AsyncClient) -> None:
    test = await _failed_test(client)

    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/punch-item",
        json={"severity": "major"},
        headers=ACTOR,
    )
    assert response.status_code == 201
    item = response.json()
    assert item["asset_id"] == test["asset_id"]
    assert item["location_id"] is None
    assert item["title"] == test["title"]
    assert item["category"] == "defect"
    assert item["severity"] == "major"
    assert item["status"] == "open"

    linked = await client.get(f"/api/v1/commissioning-tests/{test['id']}")
    assert linked.json()["punch_item_id"] == item["id"]


async def test_punch_item_accepts_overrides(client: AsyncClient) -> None:
    test = await _failed_test(client)

    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/punch-item",
        json={
            "severity": "blocking",
            "category": "safety",
            "title": "Alarm chain does not reach the head end",
            "description": "Float switch wiring suspected.",
            "due_date": "2026-09-01",
        },
        headers=ACTOR,
    )
    assert response.status_code == 201
    item = response.json()
    assert item["title"] == "Alarm chain does not reach the head end"
    assert item["category"] == "safety"
    assert item["severity"] == "blocking"
    assert item["description"] == "Float switch wiring suspected."
    assert item["due_date"] == "2026-09-01"


@pytest.mark.parametrize("state", ["pending", "passed"])
async def test_punch_item_requires_a_failed_test(client: AsyncClient, state: str) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])
    if state == "passed":
        await client.post(
            f"/api/v1/commissioning-tests/{test['id']}/pass",
            json={"witness": WITNESS},
            headers=ACTOR,
        )

    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/punch-item",
        json={"severity": "major"},
        headers=ACTOR,
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "commissioning_test.not_failed"


async def test_punch_item_only_once_per_test(client: AsyncClient) -> None:
    test = await _failed_test(client)
    first = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/punch-item",
        json={"severity": "major"},
        headers=ACTOR,
    )
    assert first.status_code == 201

    second = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/punch-item",
        json={"severity": "minor"},
        headers=ACTOR,
    )
    assert second.status_code == 409
    assert second.json()["error_code"] == "commissioning_test.punch_item_exists"


async def test_punch_item_audit_links_both_ways(client: AsyncClient) -> None:
    test = await _failed_test(client)
    response = await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/punch-item",
        json={"severity": "major"},
        headers=ACTOR,
    )
    item_id = response.json()["id"]

    test_entries = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "commissioning_test", "entity_id": test["id"]},
    )
    link_entry = test_entries.json()["items"][0]
    assert link_entry["action"] == "punch_item_created"
    assert link_entry["before"] == {"punch_item_id": None}
    assert link_entry["after"] == {"punch_item_id": item_id}

    item_entries = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "punch_item", "entity_id": item_id},
    )
    actions = [entry["action"] for entry in item_entries.json()["items"]]
    assert actions == ["created_from_commissioning_test", "created"]
    back_link = item_entries.json()["items"][0]
    assert back_link["after"] == {"commissioning_test_id": test["id"]}


async def test_commissioning_audit_trail(client: AsyncClient) -> None:
    asset = await _asset(client)
    test = await create_commissioning_test(client, asset["id"])
    await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/evidence",
        json={"note": "Baseline readings captured."},
        headers=ACTOR,
    )
    await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/pass",
        json={"witness": WITNESS, "evidence_note": "Witnessed the final transfer."},
        headers=ACTOR,
    )

    response = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "commissioning_test", "entity_id": test["id"]},
    )
    entries: list[dict[str, Any]] = response.json()["items"]
    assert [entry["action"] for entry in entries] == [
        "status_changed",
        "evidence_added",
        "evidence_added",
        "created",
    ]

    recording = entries[0]
    assert recording["before"] == {
        "status": "pending",
        "executed_by": None,
        "witnessed_by": None,
    }
    assert recording["after"] == {
        "status": "passed",
        "executed_by": "test-engineer",
        "witnessed_by": WITNESS,
    }
    assert entries[1]["after"]["note"] == "Witnessed the final transfer."
    assert entries[2]["after"]["note"] == "Baseline readings captured."


async def test_list_tests_with_filters(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset_1 = await create_asset(client, hierarchy["room"]["id"], status="installed")
    asset_2 = await create_asset(
        client, hierarchy["room"]["id"], tag="CRAC-C-01", asset_type="crac"
    )
    await create_commissioning_test(client, asset_1["id"])
    test = await create_commissioning_test(client, asset_2["id"], title="Airflow verification")
    await client.post(
        f"/api/v1/commissioning-tests/{test['id']}/pass",
        json={"witness": WITNESS},
        headers=ACTOR,
    )

    by_asset = await client.get("/api/v1/commissioning-tests", params={"asset_id": asset_1["id"]})
    assert by_asset.json()["total"] == 1

    by_status = await client.get("/api/v1/commissioning-tests", params={"status": "passed"})
    assert by_status.json()["total"] == 1
    assert by_status.json()["items"][0]["title"] == "Airflow verification"

    combined = await client.get(
        "/api/v1/commissioning-tests",
        params={"asset_id": asset_1["id"], "status": "passed"},
    )
    assert combined.json()["total"] == 0


async def test_get_missing_test(client: AsyncClient) -> None:
    response = await client.get("/api/v1/commissioning-tests/missing-id")
    assert response.status_code == 404
    assert response.json()["error_code"] == "commissioning_test.not_found"
