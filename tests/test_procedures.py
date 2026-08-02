from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import ACTOR, APPROVER, create_approved_procedure, create_procedure

pytestmark = pytest.mark.anyio


async def test_create_procedure_starts_as_draft_version_one(client: AsyncClient) -> None:
    procedure = await create_procedure(client, kind="sop", title="Data hall access")
    assert procedure["kind"] == "sop"
    assert procedure["status"] == "draft"
    assert procedure["version"] == 1
    assert procedure["predecessor_id"] is None
    assert procedure["last_edited_by"] == "test-engineer"
    assert procedure["approved_by"] is None
    assert procedure["steps"][0] == "Isolate and lock out the fan module power feed."


async def test_create_procedure_rejects_empty_step_list(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/procedures",
        json={"kind": "mop", "title": "Empty", "steps": []},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "api.validation_failed"


async def test_create_procedure_rejects_blank_step_text(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/procedures",
        json={"kind": "mop", "title": "Blank step", "steps": ["Do the work.", "   "]},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "procedure.step_text_required"


async def test_update_title_and_steps_in_draft(client: AsyncClient) -> None:
    procedure = await create_procedure(client)
    response = await client.patch(
        f"/api/v1/procedures/{procedure['id']}",
        json={"title": "Fan module replacement (rev A)", "steps": ["One.", "Two."]},
        headers=ACTOR,
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Fan module replacement (rev A)"
    assert response.json()["steps"] == ["One.", "Two."]
    assert response.json()["status"] == "draft"


async def test_update_rejects_blank_step_text(client: AsyncClient) -> None:
    procedure = await create_procedure(client)
    response = await client.patch(
        f"/api/v1/procedures/{procedure['id']}",
        json={"steps": [" "]},
        headers=ACTOR,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "procedure.step_text_required"


async def test_update_rejected_outside_draft(client: AsyncClient) -> None:
    procedure = await create_approved_procedure(client)
    rejected = await client.patch(
        f"/api/v1/procedures/{procedure['id']}",
        json={"title": "Too late"},
        headers=ACTOR,
    )
    assert rejected.status_code == 409
    assert rejected.json()["error_code"] == "procedure.not_editable"

    await client.post(f"/api/v1/procedures/{procedure['id']}/retire", headers=ACTOR)
    still_rejected = await client.patch(
        f"/api/v1/procedures/{procedure['id']}",
        json={"title": "Much too late"},
        headers=ACTOR,
    )
    assert still_rejected.status_code == 409
    assert still_rejected.json()["error_code"] == "procedure.not_editable"


async def test_approve_by_distinct_actor(client: AsyncClient) -> None:
    procedure = await create_procedure(client)
    response = await client.post(f"/api/v1/procedures/{procedure['id']}/approve", headers=APPROVER)
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["approved_by"] == "test-approver"


async def test_approve_rejected_for_last_draft_author(client: AsyncClient) -> None:
    procedure = await create_procedure(client)
    response = await client.post(f"/api/v1/procedures/{procedure['id']}/approve", headers=ACTOR)
    assert response.status_code == 409
    assert response.json()["error_code"] == "procedure.approver_must_differ"

    unchanged = await client.get(f"/api/v1/procedures/{procedure['id']}")
    assert unchanged.json()["status"] == "draft"
    assert unchanged.json()["approved_by"] is None


async def test_last_draft_edit_moves_the_approval_block(client: AsyncClient) -> None:
    # The creator drafts, the reviewer edits: now the reviewer is the last
    # author and the creator becomes an acceptable approver.
    procedure = await create_procedure(client)
    edited = await client.patch(
        f"/api/v1/procedures/{procedure['id']}",
        json={"steps": ["Reviewed step."]},
        headers=APPROVER,
    )
    assert edited.status_code == 200
    assert edited.json()["last_edited_by"] == "test-approver"

    rejected = await client.post(f"/api/v1/procedures/{procedure['id']}/approve", headers=APPROVER)
    assert rejected.status_code == 409
    assert rejected.json()["error_code"] == "procedure.approver_must_differ"

    approved = await client.post(f"/api/v1/procedures/{procedure['id']}/approve", headers=ACTOR)
    assert approved.status_code == 200
    assert approved.json()["approved_by"] == "test-engineer"


async def test_approve_only_from_draft(client: AsyncClient) -> None:
    procedure = await create_approved_procedure(client)
    response = await client.post(f"/api/v1/procedures/{procedure['id']}/approve", headers=APPROVER)
    assert response.status_code == 409
    assert response.json()["error_code"] == "procedure.invalid_transition"


async def test_retire_only_from_approved(client: AsyncClient) -> None:
    draft = await create_procedure(client)
    rejected = await client.post(f"/api/v1/procedures/{draft['id']}/retire", headers=ACTOR)
    assert rejected.status_code == 409
    assert rejected.json()["error_code"] == "procedure.invalid_transition"

    approved = await create_approved_procedure(client, title="Retiring soon")
    retired = await client.post(f"/api/v1/procedures/{approved['id']}/retire", headers=ACTOR)
    assert retired.status_code == 200
    assert retired.json()["status"] == "retired"


async def test_new_version_starts_as_linked_draft(client: AsyncClient) -> None:
    version_1 = await create_approved_procedure(client)
    response = await client.post(f"/api/v1/procedures/{version_1['id']}/new-version", headers=ACTOR)
    assert response.status_code == 201
    version_2 = response.json()
    assert version_2["id"] != version_1["id"]
    assert version_2["status"] == "draft"
    assert version_2["version"] == 2
    assert version_2["predecessor_id"] == version_1["id"]
    assert version_2["kind"] == version_1["kind"]
    assert version_2["title"] == version_1["title"]
    assert version_2["steps"] == version_1["steps"]
    assert version_2["approved_by"] is None
    assert version_2["last_edited_by"] == "test-engineer"

    unchanged = await client.get(f"/api/v1/procedures/{version_1['id']}")
    assert unchanged.json()["status"] == "approved"


@pytest.mark.parametrize("prepare", ["draft", "retired"])
async def test_new_version_requires_an_approved_procedure(
    client: AsyncClient, prepare: str
) -> None:
    if prepare == "draft":
        procedure = await create_procedure(client)
    else:
        procedure = await create_approved_procedure(client)
        await client.post(f"/api/v1/procedures/{procedure['id']}/retire", headers=ACTOR)
    response = await client.post(f"/api/v1/procedures/{procedure['id']}/new-version", headers=ACTOR)
    assert response.status_code == 409
    assert response.json()["error_code"] == "procedure.not_approved"


async def test_new_version_only_once_per_procedure(client: AsyncClient) -> None:
    version_1 = await create_approved_procedure(client)
    first = await client.post(f"/api/v1/procedures/{version_1['id']}/new-version", headers=ACTOR)
    assert first.status_code == 201
    second = await client.post(f"/api/v1/procedures/{version_1['id']}/new-version", headers=ACTOR)
    assert second.status_code == 409
    assert second.json()["error_code"] == "procedure.successor_exists"


async def test_version_chain_is_queryable_from_any_member(client: AsyncClient) -> None:
    version_1 = await create_approved_procedure(client)
    version_2_response = await client.post(
        f"/api/v1/procedures/{version_1['id']}/new-version", headers=ACTOR
    )
    version_2 = version_2_response.json()
    await client.post(f"/api/v1/procedures/{version_2['id']}/approve", headers=APPROVER)
    version_3_response = await client.post(
        f"/api/v1/procedures/{version_2['id']}/new-version", headers=ACTOR
    )
    version_3 = version_3_response.json()

    expected_ids = [version_1["id"], version_2["id"], version_3["id"]]
    for member_id in expected_ids:
        chain = await client.get(f"/api/v1/procedures/{member_id}/versions")
        assert chain.status_code == 200
        assert [version["id"] for version in chain.json()] == expected_ids
        assert [version["version"] for version in chain.json()] == [1, 2, 3]


async def test_list_procedures_with_filters(client: AsyncClient) -> None:
    await create_procedure(client, kind="mop", title="MOP draft")
    await create_approved_procedure(client, kind="sop", title="SOP approved")
    await create_procedure(client, kind="sop", title="SOP draft")

    by_kind = await client.get("/api/v1/procedures", params={"kind": "sop"})
    assert by_kind.json()["total"] == 2

    by_status = await client.get("/api/v1/procedures", params={"status": "approved"})
    assert by_status.json()["total"] == 1
    assert by_status.json()["items"][0]["title"] == "SOP approved"

    combined = await client.get("/api/v1/procedures", params={"kind": "mop", "status": "approved"})
    assert combined.json()["total"] == 0


async def test_get_missing_procedure(client: AsyncClient) -> None:
    response = await client.get("/api/v1/procedures/missing-id")
    assert response.status_code == 404
    assert response.json()["error_code"] == "procedure.not_found"


async def test_procedure_audit_trail(client: AsyncClient) -> None:
    procedure = await create_procedure(client)
    await client.patch(
        f"/api/v1/procedures/{procedure['id']}",
        json={"steps": ["Only step."]},
        headers=ACTOR,
    )
    await client.post(f"/api/v1/procedures/{procedure['id']}/approve", headers=APPROVER)

    response = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "procedure", "entity_id": procedure["id"]},
    )
    entries: list[dict[str, Any]] = response.json()["items"]
    assert [entry["action"] for entry in entries] == ["status_changed", "updated", "created"]

    approval, update, creation = entries
    assert creation["actor"] == "test-engineer"
    assert creation["before"] is None
    assert creation["after"]["status"] == "draft"
    assert creation["after"]["version"] == 1

    assert update["before"]["steps"] == procedure["steps"]
    assert update["after"]["steps"] == ["Only step."]

    assert approval["actor"] == "test-approver"
    assert approval["before"] == {"status": "draft", "approved_by": None}
    assert approval["after"] == {"status": "approved", "approved_by": "test-approver"}
