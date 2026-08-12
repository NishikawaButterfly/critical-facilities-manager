"""File evidence: verifiable uploads attached to domain objects.

Uploads are hashed server-side while they stream in, stored write-once
under their SHA-256, attached to commissioning tests, punch items,
maintenance orders, or work permits as audited acts carrying the hash,
and re-verified on every download so corruption is refused, never served.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from cfm.evidence import EvidenceStore

from .conftest import (
    ACTOR,
    VIEWER,
    build_app,
    create_asset,
    create_commissioning_test,
    create_hierarchy,
    create_order,
    create_permit,
    create_punch_item,
    create_scoped_user,
)

pytestmark = pytest.mark.anyio

CONTENT = b"thermal image of the UPS output breaker after the load test"


async def upload_evidence(
    client: AsyncClient,
    path: str,
    *,
    content: bytes = CONTENT,
    filename: str = "breaker-thermal.jpg",
    content_type: str = "image/jpeg",
    note: str | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    data = {} if note is None else {"note": note}
    return await client.post(
        path,
        files={"file": (filename, content, content_type)},
        data=data,
        headers=headers or ACTOR,
    )


async def commissioning_test_on_new_asset(client: AsyncClient) -> dict[str, Any]:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    test: dict[str, Any] = await create_commissioning_test(client, asset["id"])
    return {"hierarchy": hierarchy, "asset": asset, "test": test}


async def audit_entries_for(client: AsyncClient, entity_type: str, entity_id: str) -> list[Any]:
    response = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": entity_type, "entity_id": entity_id},
    )
    assert response.status_code == 200, response.text
    items: list[Any] = response.json()["items"]
    return items


async def test_upload_records_the_hash_of_what_was_attached(client: AsyncClient) -> None:
    scene = await commissioning_test_on_new_asset(client)
    test_id = scene["test"]["id"]

    response = await upload_evidence(
        client,
        f"/api/v1/commissioning-tests/{test_id}/evidence-files",
        note="Thermal scan after the transfer",
    )
    assert response.status_code == 201, response.text
    attachment = response.json()
    expected_sha = hashlib.sha256(CONTENT).hexdigest()
    assert attachment["evidence_object"]["sha256"] == expected_sha
    assert attachment["evidence_object"]["size_bytes"] == len(CONTENT)
    assert attachment["evidence_object"]["filename"] == "breaker-thermal.jpg"
    assert attachment["evidence_object"]["content_type"] == "image/jpeg"
    assert attachment["evidence_object"]["uploaded_by"] == "test-engineer"
    assert attachment["commissioning_test_id"] == test_id
    assert attachment["attached_by"] == "test-engineer"
    assert attachment["note"] == "Thermal scan after the transfer"

    listed = await client.get(f"/api/v1/commissioning-tests/{test_id}/evidence-files")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [attachment["id"]]

    entries = await audit_entries_for(client, "commissioning_test", test_id)
    attach_entries = [entry for entry in entries if entry["action"] == "evidence_attached"]
    assert len(attach_entries) == 1
    recorded = attach_entries[0]
    assert recorded["actor"] == "test-engineer"
    assert recorded["scope"] == "installation"
    assert recorded["after"]["sha256"] == expected_sha
    assert recorded["after"]["filename"] == "breaker-thermal.jpg"
    assert recorded["after"]["note"] == "Thermal scan after the transfer"
    assert recorded["after"]["content_already_stored"] is False


async def test_identical_content_dedupes_into_one_object(client: AsyncClient) -> None:
    scene = await commissioning_test_on_new_asset(client)
    test_id = scene["test"]["id"]
    item = await create_punch_item(client, asset_id=scene["asset"]["id"])

    first = await upload_evidence(client, f"/api/v1/commissioning-tests/{test_id}/evidence-files")
    second = await upload_evidence(
        client,
        f"/api/v1/punch-items/{item['id']}/evidence-files",
        filename="same-bytes-different-name.jpg",
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    first_object = first.json()["evidence_object"]
    second_object = second.json()["evidence_object"]
    assert second_object["id"] == first_object["id"]
    assert second_object["sha256"] == first_object["sha256"]
    # The object keeps the declaration made when the content first arrived;
    # the second attach's own declaration lives in its audit entry.
    assert second_object["filename"] == "breaker-thermal.jpg"
    entries = await audit_entries_for(client, "punch_item", item["id"])
    attach_entry = next(entry for entry in entries if entry["action"] == "evidence_attached")
    assert attach_entry["after"]["filename"] == "same-bytes-different-name.jpg"
    assert attach_entry["after"]["content_already_stored"] is True


async def test_the_same_object_attaches_to_one_target_only_once(client: AsyncClient) -> None:
    scene = await commissioning_test_on_new_asset(client)
    test_id = scene["test"]["id"]

    first = await upload_evidence(client, f"/api/v1/commissioning-tests/{test_id}/evidence-files")
    assert first.status_code == 201, first.text
    repeat = await upload_evidence(client, f"/api/v1/commissioning-tests/{test_id}/evidence-files")
    assert repeat.status_code == 409
    assert repeat.json()["error_code"] == "evidence.already_attached"


async def test_orders_and_permits_take_evidence_too(client: AsyncClient) -> None:
    hierarchy = await create_hierarchy(client)
    asset = await create_asset(client, hierarchy["room"]["id"])
    order = await create_order(client, asset["id"])
    scheduled = await client.post(f"/api/v1/maintenance-orders/{order['id']}/schedule")
    assert scheduled.status_code == 200, scheduled.text
    permit = await create_permit(client, order["id"])

    on_order = await upload_evidence(
        client,
        f"/api/v1/maintenance-orders/{order['id']}/evidence-files",
        content=b"work area photo before isolation",
        filename="work-area.jpg",
    )
    assert on_order.status_code == 201, on_order.text
    assert on_order.json()["order_id"] == order["id"]

    on_permit = await upload_evidence(
        client,
        f"/api/v1/work-permits/{permit['id']}/evidence-files",
        content=b"signed lockout-tagout sheet",
        filename="loto-sheet.pdf",
        content_type="application/pdf",
    )
    assert on_permit.status_code == 201, on_permit.text
    assert on_permit.json()["permit_id"] == permit["id"]

    listed = await client.get(f"/api/v1/work-permits/{permit['id']}/evidence-files")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


async def test_download_returns_the_exact_bytes_as_an_attachment(client: AsyncClient) -> None:
    scene = await commissioning_test_on_new_asset(client)
    uploaded = await upload_evidence(
        client, f"/api/v1/commissioning-tests/{scene['test']['id']}/evidence-files"
    )
    object_id = uploaded.json()["evidence_object"]["id"]

    metadata = await client.get(f"/api/v1/evidence-objects/{object_id}")
    assert metadata.status_code == 200
    assert metadata.json()["sha256"] == hashlib.sha256(CONTENT).hexdigest()

    downloaded = await client.get(f"/api/v1/evidence-objects/{object_id}/content")
    assert downloaded.status_code == 200
    assert downloaded.content == CONTENT
    assert downloaded.headers["content-type"] == "image/jpeg"
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert "breaker-thermal.jpg" in downloaded.headers["content-disposition"]
    assert downloaded.headers["x-content-type-options"] == "nosniff"


async def test_an_unknown_evidence_object_is_404(client: AsyncClient) -> None:
    missing = str(uuid4())
    metadata = await client.get(f"/api/v1/evidence-objects/{missing}")
    assert metadata.status_code == 404
    assert metadata.json()["error_code"] == "evidence.not_found"
    content = await client.get(f"/api/v1/evidence-objects/{missing}/content")
    assert content.status_code == 404


async def test_a_corrupted_object_is_refused_not_served(client: AsyncClient, app: FastAPI) -> None:
    scene = await commissioning_test_on_new_asset(client)
    uploaded = await upload_evidence(
        client, f"/api/v1/commissioning-tests/{scene['test']['id']}/evidence-files"
    )
    evidence_object = uploaded.json()["evidence_object"]

    store = EvidenceStore(app.state.settings.evidence_dir)
    path = store.object_path(evidence_object["sha256"])
    tampered = bytearray(path.read_bytes())
    tampered[3] ^= 0xFF
    path.write_bytes(bytes(tampered))

    refused = await client.get(f"/api/v1/evidence-objects/{evidence_object['id']}/content")
    assert refused.status_code == 500
    body = refused.json()
    assert body["error_code"] == "evidence.corrupted"
    assert evidence_object["sha256"] in body["detail"]
    assert "will not be served" in body["detail"]


async def test_an_empty_upload_proves_nothing_and_is_rejected(client: AsyncClient) -> None:
    scene = await commissioning_test_on_new_asset(client)
    response = await upload_evidence(
        client,
        f"/api/v1/commissioning-tests/{scene['test']['id']}/evidence-files",
        content=b"",
        filename="empty.txt",
        content_type="text/plain",
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "evidence.empty"


async def test_attaching_to_a_terminal_target_conflicts(client: AsyncClient) -> None:
    scene = await commissioning_test_on_new_asset(client)
    test_id = scene["test"]["id"]
    passed = await client.post(
        f"/api/v1/commissioning-tests/{test_id}/pass", json={"witness": "test-witness"}
    )
    assert passed.status_code == 200, passed.text

    response = await upload_evidence(
        client, f"/api/v1/commissioning-tests/{test_id}/evidence-files"
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "evidence.target_not_active"


async def test_a_viewer_cannot_attach_evidence(client: AsyncClient) -> None:
    scene = await commissioning_test_on_new_asset(client)
    response = await upload_evidence(
        client,
        f"/api/v1/commissioning-tests/{scene['test']['id']}/evidence-files",
        headers=VIEWER,
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "auth.forbidden"


async def test_attaching_follows_site_scope_and_reading_does_not(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await create_hierarchy(client, "B")
    asset_b = await create_asset(client, site_b["room"]["id"], tag="UPS-B-01")
    test_b = await create_commissioning_test(client, asset_b["id"])
    _, eng_a = await create_scoped_user(client, "site-a-eng", site_ids=[site_a["site"]["id"]])

    forbidden = await upload_evidence(
        client,
        f"/api/v1/commissioning-tests/{test_b['id']}/evidence-files",
        headers=eng_a,
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error_code"] == "auth.scope_forbidden"

    # Attached by a user with authority on site B, the evidence is still
    # readable installation-wide: downloads are reads, and reads are not
    # site-scoped anywhere in this API.
    attached = await upload_evidence(
        client, f"/api/v1/commissioning-tests/{test_b['id']}/evidence-files"
    )
    assert attached.status_code == 201, attached.text
    object_id = attached.json()["evidence_object"]["id"]
    entries = await audit_entries_for(client, "commissioning_test", test_b["id"])
    attach_entry = next(entry for entry in entries if entry["action"] == "evidence_attached")
    assert attach_entry["scope"] == "installation"

    readable = await client.get(f"/api/v1/evidence-objects/{object_id}/content", headers=eng_a)
    assert readable.status_code == 200
    assert readable.content == CONTENT


@pytest.fixture
async def small_cap_app(database_url: str, tmp_path: Path) -> AsyncIterator[FastAPI]:
    """An application whose evidence uploads are capped at one mebibyte."""
    async with build_app(
        database_url, evidence_dir=tmp_path / "evidence", evidence_max_mb=1
    ) as application:
        yield application


@pytest.fixture
async def small_cap_client(small_cap_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=small_cap_app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", headers=ACTOR
    ) as test_client:
        yield test_client


async def test_a_file_over_the_cap_is_rejected_with_413(small_cap_client: AsyncClient) -> None:
    """Just over the per-file cap, under the transport limit: the endpoint rejects."""
    scene = await commissioning_test_on_new_asset(small_cap_client)
    response = await upload_evidence(
        small_cap_client,
        f"/api/v1/commissioning-tests/{scene['test']['id']}/evidence-files",
        content=b"\0" * (1024 * 1024 + 1),
        filename="too-big.bin",
        content_type="application/octet-stream",
    )
    assert response.status_code == 413
    assert response.json()["error_code"] == "evidence.file_too_large"


async def test_an_oversized_body_is_rejected_before_parsing(
    small_cap_client: AsyncClient,
) -> None:
    """Far over the transport limit: Content-Length alone rejects the request."""
    scene = await commissioning_test_on_new_asset(small_cap_client)
    response = await upload_evidence(
        small_cap_client,
        f"/api/v1/commissioning-tests/{scene['test']['id']}/evidence-files",
        content=b"\0" * (3 * 1024 * 1024),
        filename="way-too-big.bin",
        content_type="application/octet-stream",
    )
    assert response.status_code == 413
    assert response.json()["error_code"] == "evidence.request_too_large"


async def test_a_chunked_oversized_body_is_cut_off_mid_stream(
    small_cap_client: AsyncClient,
) -> None:
    """No Content-Length header: the streamed byte count enforces the limit."""
    scene = await commissioning_test_on_new_asset(small_cap_client)
    boundary = "cfm-evidence-boundary"
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="stream.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    async def body() -> AsyncIterator[bytes]:
        yield head
        chunk = b"\0" * (256 * 1024)
        for _ in range(12):  # three mebibytes, well past the limit
            yield chunk
        yield tail

    response = await small_cap_client.post(
        f"/api/v1/commissioning-tests/{scene['test']['id']}/evidence-files",
        content=body(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **ACTOR},
    )
    assert response.status_code == 413
    assert response.json()["error_code"] == "evidence.request_too_large"
