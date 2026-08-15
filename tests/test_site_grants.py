"""Site-scoped write authority.

A user's role says what they may do; their site grants say where their
authority applies. An installation-wide grant (``site_id`` null) covers
every site; a site grant covers that site's whole subtree. Objects that
belong to no single site (procedures, constraints, sites themselves)
require an installation-wide grant. The reading half of the same rule has
its own file, tests/test_read_scope.py.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import event

from .conftest import (
    ACTOR,
    ADMIN,
    create_asset,
    create_hierarchy,
    create_order,
    create_punch_item,
    create_scoped_user,
)

pytestmark = pytest.mark.anyio


async def test_site_scoped_engineer_cannot_write_on_another_site(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await create_hierarchy(client, "B")
    asset_b = await create_asset(client, site_b["room"]["id"], tag="UPS-B-01")
    _, eng_a = await create_scoped_user(client, "site-a-eng", site_ids=[site_a["site"]["id"]])

    renamed = await client.patch(
        f"/api/v1/assets/{asset_b['id']}", json={"name": "Renamed"}, headers=eng_a
    )
    assert renamed.status_code == 403
    assert renamed.json()["error_code"] == "auth.scope_forbidden"

    transitioned = await client.post(
        f"/api/v1/assets/{asset_b['id']}/transition",
        json={"to_status": "installed"},
        headers=eng_a,
    )
    assert transitioned.status_code == 403

    created = await client.post(
        "/api/v1/assets",
        json={
            "tag": "CRAC-B-01",
            "name": "CRAC Unit 1",
            "asset_type": "crac",
            "criticality": "high",
            "location_id": site_b["room"]["id"],
        },
        headers=eng_a,
    )
    assert created.status_code == 403

    order = await client.post(
        "/api/v1/maintenance-orders",
        json={
            "order_type": "preventive",
            "asset_id": asset_b["id"],
            "title": "Inspection",
            "due_date": "2026-09-01",
        },
        headers=eng_a,
    )
    assert order.status_code == 403

    incident = await client.post(
        "/api/v1/incidents",
        json={"asset_id": asset_b["id"], "severity": "low", "title": "Alarm"},
        headers=eng_a,
    )
    assert incident.status_code == 403

    # The same user retains full write authority on their own site.
    asset_a = await create_asset(client, site_a["room"]["id"], tag="UPS-A-01")
    allowed = await client.patch(
        f"/api/v1/assets/{asset_a['id']}", json={"name": "Renamed"}, headers=eng_a
    )
    assert allowed.status_code == 200

    # Reads follow the same grants: site B's asset is not there to be read,
    # and answers what a missing asset answers (see tests/test_read_scope.py).
    fetched = await client.get(f"/api/v1/assets/{asset_b['id']}", headers=eng_a)
    assert fetched.status_code == 404
    assert fetched.json()["error_code"] == "asset.not_found"


async def test_site_grant_covers_the_whole_site_subtree(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    _, eng_a = await create_scoped_user(client, "subtree-eng", site_ids=[site_a["site"]["id"]])

    # An asset in a room three levels below the site root.
    created = await client.post(
        "/api/v1/assets",
        json={
            "tag": "PDU-A-01",
            "name": "PDU 1",
            "asset_type": "pdu",
            "criticality": "medium",
            "location_id": site_a["room"]["id"],
        },
        headers=eng_a,
    )
    assert created.status_code == 201, created.text

    # A punch item scoped to the room location.
    punch = await client.post(
        "/api/v1/punch-items",
        json={
            "location_id": site_a["room"]["id"],
            "category": "documentation",
            "severity": "minor",
            "title": "Missing cable schedule",
        },
        headers=eng_a,
    )
    assert punch.status_code == 201, punch.text

    # Growing and renaming the tree below the site root.
    room = await client.post(
        "/api/v1/locations",
        json={
            "kind": "room",
            "code": "L1-DH2",
            "name": "Data Hall 2",
            "parent_id": site_a["floor"]["id"],
        },
        headers=eng_a,
    )
    assert room.status_code == 201, room.text
    renamed = await client.patch(
        f"/api/v1/locations/{site_a['building']['id']}",
        json={"name": "Renamed Building"},
        headers=eng_a,
    )
    assert renamed.status_code == 200


async def test_installation_wide_grant_works_on_every_site(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await create_hierarchy(client, "B")
    # The fixture engineer holds an installation-wide grant.
    await create_asset(client, site_a["room"]["id"], tag="UPS-A-01")
    await create_asset(client, site_b["room"]["id"], tag="UPS-B-01")

    # A user created without explicit site scopes defaults to installation-wide.
    _, eng = await create_scoped_user(client, "default-eng")
    for hierarchy, tag in ((site_a, "GEN-A-01"), (site_b, "GEN-B-01")):
        response = await client.post(
            "/api/v1/assets",
            json={
                "tag": tag,
                "name": "Generator",
                "asset_type": "generator",
                "criticality": "high",
                "location_id": hierarchy["room"]["id"],
            },
            headers=eng,
        )
        assert response.status_code == 201, response.text


async def test_unsited_objects_require_an_installation_wide_grant(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    asset_a = await create_asset(client, site_a["room"]["id"], tag="UPS-A-01")
    _, eng_a = await create_scoped_user(client, "site-only-eng", site_ids=[site_a["site"]["id"]])

    procedure = await client.post(
        "/api/v1/procedures",
        json={"kind": "sop", "title": "Access control", "steps": ["Check the list."]},
        headers=eng_a,
    )
    assert procedure.status_code == 403
    assert procedure.json()["error_code"] == "auth.scope_forbidden"

    # Constraints span sites, so they are installation objects even when
    # every member asset lives on the granted site.
    constraint = await client.post(
        "/api/v1/constraints",
        json={
            "kind": "advisory",
            "name": "Maintenance window",
            "asset_ids": [asset_a["id"]],
        },
        headers=eng_a,
    )
    assert constraint.status_code == 403

    # Creating a site changes the set of scopable sites: installation authority.
    new_site = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-N", "name": "Campus N"},
        headers=eng_a,
    )
    assert new_site.status_code == 403

    # So does deleting one, even an empty site.
    empty = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-E", "name": "Campus E"},
        headers=ACTOR,
    )
    assert empty.status_code == 201
    deleted = await client.delete(f"/api/v1/locations/{empty.json()['id']}", headers=eng_a)
    assert deleted.status_code == 403

    # An installation-wide engineer creates procedures as before.
    allowed = await client.post(
        "/api/v1/procedures",
        json={"kind": "sop", "title": "Access control", "steps": ["Check the list."]},
        headers=ACTOR,
    )
    assert allowed.status_code == 201


async def test_moving_an_asset_across_sites_requires_both_grants(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await create_hierarchy(client, "B")
    asset = await create_asset(client, site_a["room"]["id"], tag="UPS-A-01")
    _, eng_a = await create_scoped_user(client, "one-site-eng", site_ids=[site_a["site"]["id"]])
    _, eng_ab = await create_scoped_user(
        client,
        "two-site-eng",
        site_ids=[site_a["site"]["id"], site_b["site"]["id"]],
    )

    denied = await client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"location_id": site_b["room"]["id"]},
        headers=eng_a,
    )
    assert denied.status_code == 403
    assert denied.json()["error_code"] == "auth.scope_forbidden"

    moved = await client.patch(
        f"/api/v1/assets/{asset['id']}",
        json={"location_id": site_b["room"]["id"]},
        headers=eng_ab,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["site_id"] == site_b["site"]["id"]


async def test_four_eyes_flows_enforce_the_second_actors_scope(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await create_hierarchy(client, "B")
    asset = await create_asset(client, site_a["room"]["id"], tag="UPS-A-01", status="operational")
    _, eng_b = await create_scoped_user(client, "site-b-eng", site_ids=[site_b["site"]["id"]])
    _, eng_a2 = await create_scoped_user(client, "site-a-second", site_ids=[site_a["site"]["id"]])

    order = await create_order(client, asset["id"])
    await client.post(f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR)
    permit = await client.post(
        "/api/v1/work-permits",
        json={"order_id": order["id"], "scope": "Enclosure only."},
        headers=ACTOR,
    )
    permit_id = permit.json()["id"]

    # The issuer differs from the requester but is scoped to the wrong site.
    denied = await client.post(f"/api/v1/work-permits/{permit_id}/issue", headers=eng_b)
    assert denied.status_code == 403
    assert denied.json()["error_code"] == "auth.scope_forbidden"

    # A second site-A engineer may issue it.
    issued = await client.post(f"/api/v1/work-permits/{permit_id}/issue", headers=eng_a2)
    assert issued.status_code == 200, issued.text

    # Punch item closing follows the same rule for the verifier.
    punch = await create_punch_item(client, asset_id=asset["id"])
    started = await client.post(f"/api/v1/punch-items/{punch['id']}/start", headers=ACTOR)
    assert started.status_code == 200
    close_denied = await client.post(
        f"/api/v1/punch-items/{punch['id']}/close",
        json={"closing_note": "Fixed."},
        headers=eng_b,
    )
    assert close_denied.status_code == 403
    close_allowed = await client.post(
        f"/api/v1/punch-items/{punch['id']}/close",
        json={"closing_note": "Fixed."},
        headers=eng_a2,
    )
    assert close_allowed.status_code == 200, close_allowed.text


async def test_audit_entries_record_the_scope_used(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    _, eng_a = await create_scoped_user(client, "audited-eng", site_ids=[site_a["site"]["id"]])

    created = await client.post(
        "/api/v1/assets",
        json={
            "tag": "UPS-A-01",
            "name": "UPS 1",
            "asset_type": "ups",
            "criticality": "critical",
            "location_id": site_a["room"]["id"],
        },
        headers=eng_a,
    )
    assert created.status_code == 201
    entries = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "asset", "entity_id": created.json()["id"]},
    )
    assert entries.json()["items"][0]["scope"] == f"site:{site_a['site']['id']}"

    # A write authorized by an installation-wide grant records that scope,
    # for site objects and unsited objects alike.
    asset = await create_asset(client, site_a["room"]["id"], tag="UPS-A-02")
    entries = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "asset", "entity_id": asset["id"]},
    )
    assert entries.json()["items"][0]["scope"] == "installation"

    procedure = await client.post(
        "/api/v1/procedures",
        json={"kind": "sop", "title": "Access", "steps": ["Check."]},
        headers=ACTOR,
    )
    entries = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "procedure", "entity_id": procedure.json()["id"]},
    )
    assert entries.json()["items"][0]["scope"] == "installation"

    # User management is not site-scoped; its entries carry no scope.
    entries = await client.get(
        "/api/v1/audit-entries", params={"entity_type": "user", "actor": "test-admin"}
    )
    assert entries.json()["total"] >= 1
    assert all(entry["scope"] is None for entry in entries.json()["items"])


async def test_grants_api_lifecycle(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await create_hierarchy(client, "B")
    user, headers = await create_scoped_user(client, "lifecycle-eng")

    listing = await client.get(f"/api/v1/users/{user['id']}/site-grants", headers=ADMIN)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["site_id"] is None
    installation_grant_id = items[0]["id"]

    duplicate = await client.post(
        f"/api/v1/users/{user['id']}/site-grants", json={"site_id": None}, headers=ADMIN
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error_code"] == "site_grant.duplicate"

    not_a_site = await client.post(
        f"/api/v1/users/{user['id']}/site-grants",
        json={"site_id": site_a["building"]["id"]},
        headers=ADMIN,
    )
    assert not_a_site.status_code == 422
    assert not_a_site.json()["error_code"] == "site_grant.not_a_site"

    missing_site = await client.post(
        f"/api/v1/users/{user['id']}/site-grants",
        json={"site_id": "no-such-location"},
        headers=ADMIN,
    )
    assert missing_site.status_code == 422
    assert missing_site.json()["error_code"] == "site_grant.site_not_found"

    granted = await client.post(
        f"/api/v1/users/{user['id']}/site-grants",
        json={"site_id": site_b["site"]["id"]},
        headers=ADMIN,
    )
    assert granted.status_code == 201, granted.text
    assert granted.json()["site_id"] == site_b["site"]["id"]

    # Deleting the installation-wide grant narrows the user to site B only,
    # effective immediately.
    deleted = await client.delete(
        f"/api/v1/users/{user['id']}/site-grants/{installation_grant_id}", headers=ADMIN
    )
    assert deleted.status_code == 204
    denied = await client.post(
        "/api/v1/assets",
        json={
            "tag": "UPS-A-01",
            "name": "UPS 1",
            "asset_type": "ups",
            "criticality": "critical",
            "location_id": site_a["room"]["id"],
        },
        headers=headers,
    )
    assert denied.status_code == 403
    allowed = await client.post(
        "/api/v1/assets",
        json={
            "tag": "UPS-B-01",
            "name": "UPS 1",
            "asset_type": "ups",
            "criticality": "critical",
            "location_id": site_b["room"]["id"],
        },
        headers=headers,
    )
    assert allowed.status_code == 201, allowed.text

    missing_grant = await client.delete(
        f"/api/v1/users/{user['id']}/site-grants/{installation_grant_id}", headers=ADMIN
    )
    assert missing_grant.status_code == 404
    assert missing_grant.json()["error_code"] == "site_grant.not_found"

    # Grant management is admin-only, like the rest of user management.
    forbidden = await client.get(f"/api/v1/users/{user['id']}/site-grants", headers=ACTOR)
    assert forbidden.status_code == 403
    assert forbidden.json()["error_code"] == "auth.forbidden"


async def test_scoped_user_creation_validates_sites_atomically(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")

    rejected = await client.post(
        "/api/v1/users",
        json={
            "username": "half-made",
            "display_name": "Half Made",
            "role": "engineer",
            "site_ids": [site_a["site"]["id"], "no-such-site"],
        },
        headers=ADMIN,
    )
    assert rejected.status_code == 422
    assert rejected.json()["error_code"] == "site_grant.site_not_found"

    # The rejected creation left nothing behind: the username is still free.
    retried = await client.post(
        "/api/v1/users",
        json={
            "username": "half-made",
            "display_name": "Half Made",
            "role": "engineer",
            "site_ids": [site_a["site"]["id"]],
        },
        headers=ADMIN,
    )
    assert retried.status_code == 201, retried.text
    listing = await client.get(f"/api/v1/users/{retried.json()['id']}/site-grants", headers=ADMIN)
    assert [item["site_id"] for item in listing.json()["items"]] == [site_a["site"]["id"]]


async def test_a_site_grant_does_not_let_a_viewer_write(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    _, viewer = await create_scoped_user(
        client, "scoped-viewer", role="viewer", site_ids=[site_a["site"]["id"]]
    )
    response = await client.post(
        "/api/v1/assets",
        json={
            "tag": "UPS-A-01",
            "name": "UPS 1",
            "asset_type": "ups",
            "criticality": "critical",
            "location_id": site_a["room"]["id"],
        },
        headers=viewer,
    )
    # The role gate rejects the write before scopes are even consulted.
    assert response.status_code == 403
    assert response.json()["error_code"] == "auth.forbidden"


async def test_deleting_a_site_with_grants_is_blocked(client: AsyncClient) -> None:
    empty = await client.post(
        "/api/v1/locations",
        json={"kind": "site", "code": "CAMP-E", "name": "Campus E"},
        headers=ACTOR,
    )
    assert empty.status_code == 201
    site_id = empty.json()["id"]
    user, _ = await create_scoped_user(client, "referencing-eng", site_ids=[site_id])

    blocked = await client.delete(f"/api/v1/locations/{site_id}", headers=ACTOR)
    assert blocked.status_code == 409
    assert blocked.json()["error_code"] == "location.has_site_grants"

    grants = await client.get(f"/api/v1/users/{user['id']}/site-grants", headers=ADMIN)
    grant_id = grants.json()["items"][0]["id"]
    await client.delete(f"/api/v1/users/{user['id']}/site-grants/{grant_id}", headers=ADMIN)
    deleted = await client.delete(f"/api/v1/locations/{site_id}", headers=ACTOR)
    assert deleted.status_code == 204


async def test_grant_resolution_runs_one_query_per_request(
    app: FastAPI, client: AsyncClient
) -> None:
    """The cross-site move checks two sites but loads the grants once."""
    site_a = await create_hierarchy(client, "A")
    site_b = await create_hierarchy(client, "B")
    asset = await create_asset(client, site_a["room"]["id"], tag="UPS-A-01")
    _, eng_ab = await create_scoped_user(
        client,
        "measured-eng",
        site_ids=[site_a["site"]["id"], site_b["site"]["id"]],
    )

    grant_queries: list[str] = []

    def track(_conn: object, _cursor: object, statement: str, *_rest: object) -> None:
        if statement.lstrip().upper().startswith("SELECT") and "site_grants" in statement:
            grant_queries.append(statement)

    engine = app.state.engine
    event.listen(engine, "before_cursor_execute", track)
    try:
        moved = await client.patch(
            f"/api/v1/assets/{asset['id']}",
            json={"location_id": site_b["room"]["id"]},
            headers=eng_ab,
        )
        assert moved.status_code == 200, moved.text
    finally:
        event.remove(engine, "before_cursor_execute", track)
    assert len(grant_queries) == 1
