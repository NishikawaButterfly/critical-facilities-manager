"""Site-scoped reads: a grant says where a user reads, not only where they write.

A user's role says what they may do; their site grants say where. Reads
follow the grants the way writes already do: a listing shows only the sites
the caller holds, its ``total`` counts only those rows, and a direct fetch of
an object on another site answers with the same 404 a missing object gets, so
an id that exists out of scope cannot be told apart from one that never
existed. Objects that belong to no single site — procedures and constraints —
stay readable, because the site-scoped workflows refer to them.

A refusal is bound by the same line as a read: a rejection must not reveal
identifiers or free text from a record the same caller could not have fetched
directly.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient, Response

from .conftest import (
    ACTOR,
    ADMIN,
    create_asset,
    create_commissioning_test,
    create_constraint,
    create_hierarchy,
    create_incident,
    create_order,
    create_permit,
    create_procedure,
    create_punch_item,
    create_scoped_user,
)

pytestmark = pytest.mark.anyio

SITE_OWNED_LISTINGS = (
    "/api/v1/locations",
    "/api/v1/assets",
    "/api/v1/maintenance-orders",
    "/api/v1/work-permits",
    "/api/v1/incidents",
    "/api/v1/commissioning-tests",
    "/api/v1/punch-items",
    "/api/v1/audit-entries",
)

EVIDENCE = b"thermal image of the output breaker after the load test"

# The fields of a maintenance order that say nothing about which order it is:
# every order has them, and the caller picked the same values for their own.
# Everything else the record carries is an identifier or somebody's words.
SHARED_ORDER_VOCABULARY = frozenset({"order_type", "status"})
"""Fields whose values are drawn from a vocabulary every order shares.

`order_type` and `status` come from fixed enumerations, so finding one of
their values in a refusal says nothing about the record that was withheld.
Everything else on a hidden order — including its `due_date`, which is its
own scheduling information — must be absent from the message.
"""


async def populate_site(client: AsyncClient, suffix: str) -> dict[str, Any]:
    """One site with one of every site-owned object hanging off it."""
    hierarchy = await create_hierarchy(client, suffix)
    asset = await create_asset(client, hierarchy["room"]["id"], tag=f"UPS-{suffix}-01")
    order = await create_order(client, asset["id"])
    scheduled = await client.post(
        f"/api/v1/maintenance-orders/{order['id']}/schedule", headers=ACTOR
    )
    assert scheduled.status_code == 200, scheduled.text
    permit = await create_permit(client, order["id"])
    incident = await create_incident(client, asset["id"])
    test = await create_commissioning_test(client, asset["id"])
    punch_item = await create_punch_item(client, asset_id=asset["id"])
    room_punch_item = await create_punch_item(client, location_id=hierarchy["room"]["id"])
    return {
        **hierarchy,
        "asset": asset,
        "order": order,
        "permit": permit,
        "incident": incident,
        "test": test,
        "punch_item": punch_item,
        "room_punch_item": room_punch_item,
    }


def object_paths(site: dict[str, Any]) -> dict[str, str]:
    """The direct-fetch path of every site-owned object of one populated site."""
    return {
        "location": f"/api/v1/locations/{site['room']['id']}",
        "asset": f"/api/v1/assets/{site['asset']['id']}",
        "order": f"/api/v1/maintenance-orders/{site['order']['id']}",
        "permit": f"/api/v1/work-permits/{site['permit']['id']}",
        "incident": f"/api/v1/incidents/{site['incident']['id']}",
        "test": f"/api/v1/commissioning-tests/{site['test']['id']}",
        "punch_item": f"/api/v1/punch-items/{site['punch_item']['id']}",
    }


def evidence_listing_paths(site: dict[str, Any]) -> dict[str, str]:
    """The evidence listing of every attachable target of one populated site."""
    return {
        "test": f"/api/v1/commissioning-tests/{site['test']['id']}/evidence-files",
        "punch_item": f"/api/v1/punch-items/{site['punch_item']['id']}/evidence-files",
        "order": f"/api/v1/maintenance-orders/{site['order']['id']}/evidence-files",
        "permit": f"/api/v1/work-permits/{site['permit']['id']}/evidence-files",
    }


def without_identifier(body: dict[str, Any], identifier: str) -> dict[str, Any]:
    """The problem body with one object id blanked out, so two can be compared."""
    return {
        key: value.replace(identifier, "<id>") if isinstance(value, str) else value
        for key, value in body.items()
    }


async def user_without_grants(client: AsyncClient, username: str) -> dict[str, str]:
    """A user whose every grant has been removed: role but nowhere to use it."""
    user, headers = await create_scoped_user(client, username)
    grants = await client.get(f"/api/v1/users/{user['id']}/site-grants", headers=ADMIN)
    for grant in grants.json()["items"]:
        removed = await client.delete(
            f"/api/v1/users/{user['id']}/site-grants/{grant['id']}", headers=ADMIN
        )
        assert removed.status_code == 204, removed.text
    return headers


async def cross_site_conflict(client: AsyncClient) -> dict[str, Any]:
    """Two sites bound into one constraint, with the site-B member under work.

    Site A carries the caller's own scheduled order; site B carries the order
    already in progress that will block it. Everything is set up with the
    installation-wide token, so the fixture never depends on the boundary the
    tests below are about.
    """
    site_a = await create_hierarchy(client, "A")
    site_b = await create_hierarchy(client, "B")
    asset_a = await create_asset(client, site_a["room"]["id"], tag="UPS-A-01")
    asset_b = await create_asset(client, site_b["room"]["id"], tag="UPS-B-01")
    constraint = await create_constraint(
        client, [asset_a["id"], asset_b["id"]], name="Cross-site redundancy pair"
    )
    blocking = await create_order(
        client,
        asset_b["id"],
        title="Site B battery string replacement",
        description="Strings 3 and 4 swapped, vendor crew on site until Friday.",
        # A due date of its own: shared with the caller's order it would be
        # indistinguishable from the default, and the symmetry test below
        # could not tell "absent" from "the same value anyway".
        due_date="2027-03-19",
    )
    scheduled = await client.post(
        f"/api/v1/maintenance-orders/{blocking['id']}/schedule", headers=ACTOR
    )
    assert scheduled.status_code == 200, scheduled.text
    started = await client.post(f"/api/v1/maintenance-orders/{blocking['id']}/start", headers=ACTOR)
    assert started.status_code == 200, started.text

    own = await create_order(client, asset_a["id"], title="Site A quarterly inspection")
    queued = await client.post(f"/api/v1/maintenance-orders/{own['id']}/schedule", headers=ACTOR)
    assert queued.status_code == 200, queued.text
    return {
        "site_a": site_a["site"],
        "site_b": site_b["site"],
        "asset_a": asset_a,
        "asset_b": asset_b,
        "constraint": constraint,
        "blocking": started.json(),
        "own": own,
    }


async def start_refused(
    client: AsyncClient, conflict: dict[str, Any], headers: dict[str, str]
) -> Response:
    """The 409 raised by starting the site-A order held up by the constraint."""
    refused = await client.post(
        f"/api/v1/maintenance-orders/{conflict['own']['id']}/start", headers=headers
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error_code"] == "maintenance_order.mutual_exclusion_conflict"
    return refused


async def upload(client: AsyncClient, path: str, content: bytes) -> dict[str, Any]:
    response = await client.post(
        path,
        files={"file": ("breaker-thermal.jpg", content, "image/jpeg")},
        headers=ACTOR,
    )
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


async def test_a_user_without_grants_reads_no_site_owned_data(client: AsyncClient) -> None:
    site = await populate_site(client, "A")
    nobody = await user_without_grants(client, "no-grant-reader")

    for path in SITE_OWNED_LISTINGS:
        page = await client.get(path, headers=nobody)
        assert page.status_code == 200, path
        assert page.json()["items"] == [], path
        assert page.json()["total"] == 0, path

    for name, path in object_paths(site).items():
        fetched = await client.get(path, headers=nobody)
        assert fetched.status_code == 404, f"{name}: {fetched.text}"


async def test_a_site_grant_hides_every_other_site(client: AsyncClient) -> None:
    site_a = await populate_site(client, "A")
    site_b = await populate_site(client, "B")
    _, eng_a = await create_scoped_user(client, "site-a-reader", site_ids=[site_a["site"]["id"]])

    for name, path in object_paths(site_a).items():
        readable = await client.get(path, headers=eng_a)
        assert readable.status_code == 200, f"{name}: {readable.text}"
    for name, path in object_paths(site_b).items():
        hidden = await client.get(path, headers=eng_a)
        assert hidden.status_code == 404, f"{name}: {hidden.text}"

    locations = await client.get("/api/v1/locations", headers=eng_a)
    visible = {item["id"] for item in locations.json()["items"]}
    assert visible == {site_a[kind]["id"] for kind in ("site", "building", "floor", "room")}
    assert locations.json()["total"] == len(visible)

    for listing, key in (
        ("/api/v1/assets", "asset"),
        ("/api/v1/maintenance-orders", "order"),
        ("/api/v1/work-permits", "permit"),
        ("/api/v1/incidents", "incident"),
        ("/api/v1/commissioning-tests", "test"),
    ):
        page = await client.get(listing, headers=eng_a)
        assert [item["id"] for item in page.json()["items"]] == [site_a[key]["id"]], listing
        assert page.json()["total"] == 1, listing

    punch_items = await client.get("/api/v1/punch-items", headers=eng_a)
    assert {item["id"] for item in punch_items.json()["items"]} == {
        site_a["punch_item"]["id"],
        site_a["room_punch_item"]["id"],
    }
    assert punch_items.json()["total"] == 2


async def test_an_out_of_scope_id_looks_exactly_like_a_missing_one(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await populate_site(client, "B")
    _, eng_a = await create_scoped_user(
        client, "indistinct-reader", site_ids=[site_a["site"]["id"]]
    )

    for name, path in object_paths(site_b).items():
        collection, _, identifier = path.rpartition("/")
        unknown = str(uuid4())
        hidden = await client.get(path, headers=eng_a)
        missing = await client.get(f"{collection}/{unknown}", headers=eng_a)
        assert hidden.status_code == missing.status_code == 404, name
        assert hidden.headers["content-type"] == missing.headers["content-type"], name
        assert without_identifier(hidden.json(), identifier) == without_identifier(
            missing.json(), unknown
        ), name


async def test_totals_and_windows_count_only_readable_rows(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await create_hierarchy(client, "B")
    for index in range(2):
        await create_asset(client, site_a["room"]["id"], tag=f"UPS-A-{index}")
    for index in range(3):
        await create_asset(client, site_b["room"]["id"], tag=f"UPS-B-{index}")
    _, eng_a = await create_scoped_user(client, "counting-reader", site_ids=[site_a["site"]["id"]])

    first = await client.get("/api/v1/assets", params={"limit": 1}, headers=eng_a)
    assert first.json()["total"] == 2
    assert len(first.json()["items"]) == 1

    second = await client.get("/api/v1/assets", params={"limit": 1, "offset": 1}, headers=eng_a)
    assert second.json()["total"] == 2
    assert len(second.json()["items"]) == 1
    assert second.json()["items"][0]["id"] != first.json()["items"][0]["id"]

    past_the_end = await client.get("/api/v1/assets", params={"offset": 2}, headers=eng_a)
    assert past_the_end.json()["items"] == []
    assert past_the_end.json()["total"] == 2

    everything = await client.get("/api/v1/assets", headers=ACTOR)
    assert everything.json()["total"] == 5


async def test_filters_do_not_reveal_another_sites_rows(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await populate_site(client, "B")
    _, eng_a = await create_scoped_user(client, "filtering-reader", site_ids=[site_a["site"]["id"]])

    probes = (
        ("/api/v1/assets", {"site_id": site_b["site"]["id"]}),
        ("/api/v1/assets", {"location_id": site_b["room"]["id"]}),
        ("/api/v1/locations", {"parent_id": site_b["site"]["id"]}),
        ("/api/v1/maintenance-orders", {"asset_id": site_b["asset"]["id"]}),
        ("/api/v1/work-permits", {"order_id": site_b["order"]["id"]}),
        ("/api/v1/incidents", {"asset_id": site_b["asset"]["id"]}),
        ("/api/v1/commissioning-tests", {"asset_id": site_b["asset"]["id"]}),
        ("/api/v1/punch-items", {"asset_id": site_b["asset"]["id"]}),
        ("/api/v1/punch-items", {"location_id": site_b["room"]["id"]}),
        ("/api/v1/audit-entries", {"entity_id": site_b["asset"]["id"]}),
        ("/api/v1/audit-entries", {"actor": "test-engineer"}),
    )
    for path, params in probes:
        page = await client.get(path, params=params, headers=eng_a)
        assert page.status_code == 200, path
        assert page.json()["items"] == [], (path, params)
        assert page.json()["total"] == 0, (path, params)


async def test_an_installation_wide_grant_still_reads_every_site(client: AsyncClient) -> None:
    site_a = await populate_site(client, "A")
    site_b = await populate_site(client, "B")
    _, everywhere = await create_scoped_user(client, "installation-reader")

    for site in (site_a, site_b):
        for name, path in object_paths(site).items():
            fetched = await client.get(path, headers=everywhere)
            assert fetched.status_code == 200, f"{name}: {fetched.text}"

    assets = await client.get("/api/v1/assets", headers=everywhere)
    assert assets.json()["total"] == 2
    orders = await client.get("/api/v1/maintenance-orders", headers=everywhere)
    assert orders.json()["total"] == 2


async def test_the_admin_role_is_no_implicit_read_bypass(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await populate_site(client, "B")
    admin_user, scoped_admin = await create_scoped_user(
        client, "site-a-admin", role="admin", site_ids=[site_a["site"]["id"]]
    )

    for name, path in object_paths(site_b).items():
        hidden = await client.get(path, headers=scoped_admin)
        assert hidden.status_code == 404, f"{name}: {hidden.text}"
    assets = await client.get("/api/v1/assets", headers=scoped_admin)
    assert assets.json()["items"] == []
    assert assets.json()["total"] == 0

    # The role still carries every administration power: what an admin may do
    # is unchanged, only where their domain reads reach is scoped.
    users = await client.get("/api/v1/users", headers=scoped_admin)
    assert users.status_code == 200
    assert users.json()["total"] >= 1
    grants = await client.get(f"/api/v1/users/{admin_user['id']}/site-grants", headers=scoped_admin)
    assert grants.status_code == 200
    assert [item["site_id"] for item in grants.json()["items"]] == [site_a["site"]["id"]]


async def test_audit_entries_follow_the_scope_recorded_at_write_time(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await populate_site(client, "B")
    _, eng_a = await create_scoped_user(client, "audited-reader", site_ids=[site_a["site"]["id"]])

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
    assert created.status_code == 201, created.text

    entries = await client.get("/api/v1/audit-entries", headers=eng_a)
    scopes = {entry["scope"] for entry in entries.json()["items"]}
    assert scopes == {f"site:{site_a['site']['id']}"}
    assert entries.json()["total"] == len(entries.json()["items"])
    assert all(entry["entity_id"] != site_b["asset"]["id"] for entry in entries.json()["items"])

    # Filtering by another site's entity, or by the actor who wrote it, adds
    # nothing: the recorded scope is what decides.
    probed = await client.get(
        "/api/v1/audit-entries",
        params={"entity_type": "asset", "entity_id": site_b["asset"]["id"]},
        headers=eng_a,
    )
    assert probed.json()["items"] == []
    assert probed.json()["total"] == 0

    # An installation-wide reader still sees the whole trail.
    everything = await client.get("/api/v1/audit-entries", headers=ACTOR)
    assert everything.json()["total"] > entries.json()["total"]
    assert any(entry["entity_id"] == site_b["asset"]["id"] for entry in everything.json()["items"])


async def test_reads_record_no_audit_entries(client: AsyncClient) -> None:
    site = await populate_site(client, "A")
    _, eng_a = await create_scoped_user(client, "quiet-reader", site_ids=[site["site"]["id"]])

    before = await client.get("/api/v1/audit-entries", params={"limit": 1}, headers=ACTOR)
    for path in SITE_OWNED_LISTINGS:
        assert (await client.get(path, headers=eng_a)).status_code == 200
    for path in object_paths(site).values():
        assert (await client.get(path, headers=eng_a)).status_code == 200
    after = await client.get("/api/v1/audit-entries", params={"limit": 1}, headers=ACTOR)
    assert after.json()["total"] == before.json()["total"]


async def test_evidence_is_not_a_way_around_the_scope(client: AsyncClient) -> None:
    site_a = await populate_site(client, "A")
    site_b = await populate_site(client, "B")
    _, eng_a = await create_scoped_user(client, "evidence-reader", site_ids=[site_a["site"]["id"]])

    attached_b = {
        name: await upload(client, path, EVIDENCE + name.encode())
        for name, path in evidence_listing_paths(site_b).items()
    }
    attached_a = await upload(client, evidence_listing_paths(site_a)["order"], EVIDENCE + b"site-a")

    for name, path in evidence_listing_paths(site_b).items():
        hidden = await client.get(path, headers=eng_a)
        assert hidden.status_code == 404, f"{name}: {hidden.text}"
    for name, attachment in attached_b.items():
        object_id = attachment["evidence_object"]["id"]
        metadata = await client.get(f"/api/v1/evidence-objects/{object_id}", headers=eng_a)
        assert metadata.status_code == 404, f"{name}: {metadata.text}"
        content = await client.get(f"/api/v1/evidence-objects/{object_id}/content", headers=eng_a)
        assert content.status_code == 404, f"{name}: {content.text}"

    # Evidence on the reader's own site is untouched: listing, metadata, and
    # the verified bytes all still answer.
    listed = await client.get(evidence_listing_paths(site_a)["order"], headers=eng_a)
    assert listed.status_code == 200
    assert [entry["id"] for entry in listed.json()] == [attached_a["id"]]
    own_object = attached_a["evidence_object"]["id"]
    metadata = await client.get(f"/api/v1/evidence-objects/{own_object}", headers=eng_a)
    assert metadata.status_code == 200, metadata.text
    content = await client.get(f"/api/v1/evidence-objects/{own_object}/content", headers=eng_a)
    assert content.status_code == 200, content.text
    assert content.content == EVIDENCE + b"site-a"


async def test_installation_scoped_objects_stay_readable(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await create_hierarchy(client, "B")
    asset_a = await create_asset(client, site_a["room"]["id"], tag="UPS-A-01")
    asset_b = await create_asset(client, site_b["room"]["id"], tag="UPS-B-01")
    procedure = await create_procedure(client)
    constraint = await create_constraint(client, [asset_a["id"], asset_b["id"]])
    _, eng_a = await create_scoped_user(client, "estate-reader", site_ids=[site_a["site"]["id"]])

    listed = await client.get("/api/v1/procedures", headers=eng_a)
    assert [item["id"] for item in listed.json()["items"]] == [procedure["id"]]
    fetched = await client.get(f"/api/v1/procedures/{procedure['id']}", headers=eng_a)
    assert fetched.status_code == 200, fetched.text
    versions = await client.get(f"/api/v1/procedures/{procedure['id']}/versions", headers=eng_a)
    assert versions.status_code == 200, versions.text

    # A constraint may span sites, so it belongs to none of them and stays
    # readable — but its membership is site-owned and shows only what the
    # reader's grants cover.
    constraints = await client.get("/api/v1/constraints", headers=eng_a)
    assert [item["id"] for item in constraints.json()["items"]] == [constraint["id"]]
    assert constraints.json()["items"][0]["asset_ids"] == [asset_a["id"]]
    single = await client.get(f"/api/v1/constraints/{constraint['id']}", headers=eng_a)
    assert single.status_code == 200, single.text
    assert single.json()["asset_ids"] == [asset_a["id"]]

    by_own_asset = await client.get(
        "/api/v1/constraints", params={"asset_id": asset_a["id"]}, headers=eng_a
    )
    assert by_own_asset.json()["total"] == 1
    by_other_asset = await client.get(
        "/api/v1/constraints", params={"asset_id": asset_b["id"]}, headers=eng_a
    )
    assert by_other_asset.json()["items"] == []
    assert by_other_asset.json()["total"] == 0

    # An installation-wide reader sees the whole membership.
    whole = await client.get(f"/api/v1/constraints/{constraint['id']}", headers=ADMIN)
    assert sorted(whole.json()["asset_ids"]) == sorted([asset_a["id"], asset_b["id"]])


async def test_only_a_write_attempt_still_distinguishes_an_out_of_scope_id(
    client: AsyncClient,
) -> None:
    """The documented edge of the scope, pinned so it cannot widen unnoticed."""
    site_a = await create_hierarchy(client, "A")
    site_b = await populate_site(client, "B")
    _, eng_a = await create_scoped_user(client, "probing-eng", site_ids=[site_a["site"]["id"]])
    _, viewer_a = await create_scoped_user(
        client, "probing-viewer", role="viewer", site_ids=[site_a["site"]["id"]]
    )
    unknown = str(uuid4())
    rename = {"name": "Renamed"}

    # An engineer's write refusal names the site, which confirms the asset.
    real = await client.patch(f"/api/v1/assets/{site_b['asset']['id']}", json=rename, headers=eng_a)
    assert real.status_code == 403
    assert real.json()["error_code"] == "auth.scope_forbidden"
    invented = await client.patch(f"/api/v1/assets/{unknown}", json=rename, headers=eng_a)
    assert invented.status_code == 404

    # A viewer never reaches that check: the role gate answers first, the same
    # way for a real id and an invented one.
    refusals = []
    for identifier in (site_b["asset"]["id"], unknown):
        refused = await client.patch(f"/api/v1/assets/{identifier}", json=rename, headers=viewer_a)
        assert refused.status_code == 403
        assert refused.json()["error_code"] == "auth.forbidden"
        refusals.append(without_identifier(refused.json(), identifier))
    assert refusals[0] == refusals[1]


async def test_a_write_against_an_unknown_id_still_answers_404(client: AsyncClient) -> None:
    """The write path loads its target the way it always did."""
    unknown = str(uuid4())
    writes = (
        (f"/api/v1/locations/{unknown}", {"name": "Renamed"}, "location.not_found"),
        (f"/api/v1/assets/{unknown}", {"name": "Renamed"}, "asset.not_found"),
        (
            f"/api/v1/maintenance-orders/{unknown}",
            {"title": "Renamed"},
            "maintenance_order.not_found",
        ),
    )
    for path, body, error_code in writes:
        missing = await client.patch(path, json=body, headers=ACTOR)
        assert missing.status_code == 404, path
        assert missing.json()["error_code"] == error_code

    transitions = (
        (f"/api/v1/work-permits/{unknown}/issue", "work_permit.not_found"),
        (f"/api/v1/incidents/{unknown}/start-investigation", "incident.not_found"),
        (f"/api/v1/commissioning-tests/{unknown}/evidence", "commissioning_test.not_found"),
        (f"/api/v1/punch-items/{unknown}/start", "punch_item.not_found"),
    )
    for path, error_code in transitions:
        missing = await client.post(path, json={"note": "x"}, headers=ACTOR)
        assert missing.status_code == 404, path
        assert missing.json()["error_code"] == error_code


@pytest.mark.parametrize("role", ["engineer", "admin"])
async def test_a_cross_site_refusal_does_not_name_the_work_it_hides(
    client: AsyncClient, role: str
) -> None:
    """Grants decide what a refusal may say — the role does not.

    An admin holding a grant on site A only reads site A only, so the
    refusal tells them exactly what it tells an engineer in the same
    position. Administering users and grants installation-wide buys no
    view of the work itself.
    """
    conflict = await cross_site_conflict(client)
    _, scoped = await create_scoped_user(
        client, f"blocked-{role}", role=role, site_ids=[conflict["site_a"]["id"]]
    )

    refused = await start_refused(client, conflict, scoped)

    blocking = conflict["blocking"]
    for field in ("id", "title", "asset_id"):
        assert blocking[field] not in refused.text, field

    # It is still a refusal: the order the caller tried to start stayed put.
    unchanged = await client.get(
        f"/api/v1/maintenance-orders/{conflict['own']['id']}", headers=scoped
    )
    assert unchanged.json()["status"] == "scheduled"


async def test_a_redacted_refusal_is_still_actionable(client: AsyncClient) -> None:
    conflict = await cross_site_conflict(client)
    _, eng_a = await create_scoped_user(client, "acting-eng", site_ids=[conflict["site_a"]["id"]])

    detail = (await start_refused(client, conflict, eng_a)).json()["detail"]

    # The constraint is installation-scoped, so naming it gives away nothing
    # a read withholds — and it is the one thing that makes the refusal
    # answerable rather than merely final.
    constraint = conflict["constraint"]
    assert constraint["name"] in detail
    assert constraint["id"] in detail
    assert "member" in detail
    assert "in progress" in detail
    assert conflict["blocking"]["id"] not in detail

    # The constraint the message names is one this caller can go and read.
    looked_up = await client.get(f"/api/v1/constraints/{constraint['id']}", headers=eng_a)
    assert looked_up.status_code == 200, looked_up.text
    assert looked_up.json()["name"] == constraint["name"]
    # And only its own side of the membership, as it always did.
    assert looked_up.json()["asset_ids"] == [conflict["asset_a"]["id"]]


@pytest.mark.parametrize("reach", ["both-sites", "installation-wide"])
async def test_a_readable_conflict_keeps_the_detailed_refusal(
    client: AsyncClient, reach: str
) -> None:
    conflict = await cross_site_conflict(client)
    site_ids = (
        None
        if reach == "installation-wide"
        else [conflict["site_a"]["id"], conflict["site_b"]["id"]]
    )
    _, headers = await create_scoped_user(client, f"reader-{reach}", site_ids=site_ids)

    detail = (await start_refused(client, conflict, headers)).json()["detail"]

    # Nothing is withheld from a caller who could have fetched the order.
    blocking = conflict["blocking"]
    for field in ("id", "title", "asset_id"):
        assert blocking[field] in detail, field
    readable = await client.get(f"/api/v1/maintenance-orders/{blocking['id']}", headers=headers)
    assert readable.status_code == 200, readable.text


async def test_a_refusal_withholds_what_the_read_withholds(client: AsyncClient) -> None:
    """Read and refusal draw the same line around the same record."""
    conflict = await cross_site_conflict(client)
    _, eng_a = await create_scoped_user(
        client, "symmetric-eng", site_ids=[conflict["site_a"]["id"]]
    )
    blocking = conflict["blocking"]
    path = f"/api/v1/maintenance-orders/{blocking['id']}"

    # The record exists and is refused to this caller alone.
    assert (await client.get(path, headers=ACTOR)).status_code == 200
    assert (await client.get(path, headers=eng_a)).status_code == 404

    refused = await start_refused(client, conflict, eng_a)

    withheld = {
        field: value
        for field, value in blocking.items()
        if isinstance(value, str) and field not in SHARED_ORDER_VOCABULARY
    }
    assert withheld.keys() >= {"id", "asset_id", "title", "description", "due_date"}
    # The blocking order carries a due date of its own, so its absence from
    # the refusal is a real assertion rather than a coincidence of fixtures.
    assert blocking["due_date"] != conflict["own"]["due_date"]
    for field, value in withheld.items():
        assert value not in refused.text, field


async def test_scoped_reads_leave_write_authorization_unchanged(client: AsyncClient) -> None:
    site_a = await create_hierarchy(client, "A")
    site_b = await populate_site(client, "B")
    _, eng_a = await create_scoped_user(client, "writing-reader", site_ids=[site_a["site"]["id"]])

    refused = await client.patch(
        f"/api/v1/assets/{site_b['asset']['id']}", json={"name": "Renamed"}, headers=eng_a
    )
    assert refused.status_code == 403
    assert refused.json()["error_code"] == "auth.scope_forbidden"

    allowed = await client.post(
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
    assert allowed.status_code == 201, allowed.text

    # A punch item scoped to a location, not an asset, resolves its site
    # through the location — on the write path as much as on the read one.
    punch_item = await create_punch_item(client, location_id=site_a["room"]["id"])
    started = await client.post(f"/api/v1/punch-items/{punch_item['id']}/start", headers=eng_a)
    assert started.status_code == 200, started.text
