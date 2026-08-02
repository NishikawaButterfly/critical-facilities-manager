from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from cfm.config import Settings
from cfm.demo import (
    SEED_ADMIN,
    SEED_CREW,
    SEED_ENGINEER,
    SEED_REVIEWER,
    SEED_VIEWER,
    SeedError,
    seed_demo,
)
from cfm.main import create_app

pytestmark = pytest.mark.anyio


def test_seed_demo_populates_a_fresh_database(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'seed.db').as_posix()}"
    result = seed_demo(database_url)
    counts = result.counts
    assert counts["users"] == 4
    assert counts["api_tokens"] == 4
    assert counts["locations"] == 7
    assert counts["assets"] == 7
    assert counts["maintenance_orders"] == 6
    assert counts["procedures"] == 4
    assert counts["work_permits"] == 2
    assert counts["incidents"] == 2
    assert counts["commissioning_tests"] == 2
    assert counts["punch_items"] == 2
    assert counts["constraints"] == 2
    # Every create plus every recorded transition, edit, evidence note, and link.
    assert counts["audit_entries"] == 65


def test_seed_demo_returns_one_token_per_crew_member(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'seed.db').as_posix()}"
    result = seed_demo(database_url)
    assert sorted(result.tokens) == sorted(username for username, _, _ in SEED_CREW)
    secrets = list(result.tokens.values())
    assert len(set(secrets)) == len(secrets)
    assert all(len(secret) > 30 for secret in secrets)


def test_seed_demo_refuses_a_non_empty_database(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'seed.db').as_posix()}"
    seed_demo(database_url)
    with pytest.raises(SeedError):
        seed_demo(database_url)


async def test_seeded_database_is_explorable_through_the_api(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'seed.db').as_posix()}"
    result = seed_demo(database_url)
    settings = Settings(database_url=database_url, docs_enabled=False)
    application = create_app(settings)
    transport = ASGITransport(app=application)
    viewer_auth = {"Authorization": f"Bearer {result.tokens[SEED_VIEWER]}"}
    async with (
        application.router.lifespan_context(application),
        AsyncClient(
            transport=transport, base_url="http://testserver", headers=viewer_auth
        ) as client,
    ):
        assets = await client.get("/api/v1/assets")
        assert assets.json()["total"] == 7

        done_orders = await client.get("/api/v1/maintenance-orders", params={"status": "done"})
        assert done_orders.json()["total"] == 1
        assert done_orders.json()["items"][0]["title"] == "Annual chiller service"

        sops = await client.get("/api/v1/procedures", params={"kind": "sop"})
        assert sops.json()["total"] == 2
        approved_sops = [item for item in sops.json()["items"] if item["status"] == "approved"]
        assert len(approved_sops) == 1
        versions = await client.get(f"/api/v1/procedures/{approved_sops[0]['id']}/versions")
        assert [version["version"] for version in versions.json()] == [1, 2]
        assert versions.json()[1]["status"] == "draft"

        issued_permits = await client.get("/api/v1/work-permits", params={"status": "issued"})
        assert issued_permits.json()["total"] == 1
        assert issued_permits.json()["items"][0]["issued_by"] == SEED_REVIEWER

        high_incidents = await client.get("/api/v1/incidents", params={"severity": "high"})
        assert high_incidents.json()["total"] == 1
        incident = high_incidents.json()["items"][0]
        assert incident["status"] == "investigating"
        assert incident["corrective_order_id"] is not None
        corrective = await client.get(
            f"/api/v1/maintenance-orders/{incident['corrective_order_id']}"
        )
        assert corrective.json()["order_type"] == "corrective"

        passed_tests = await client.get("/api/v1/commissioning-tests", params={"status": "passed"})
        assert passed_tests.json()["total"] == 1
        passed_test = passed_tests.json()["items"][0]
        assert passed_test["witnessed_by"] == SEED_REVIEWER
        assert len(passed_test["evidence"]) == 3

        failed_tests = await client.get("/api/v1/commissioning-tests", params={"status": "failed"})
        assert failed_tests.json()["total"] == 1
        failed_test = failed_tests.json()["items"][0]
        assert failed_test["punch_item_id"] is not None
        linked_punch = await client.get(f"/api/v1/punch-items/{failed_test['punch_item_id']}")
        assert linked_punch.json()["asset_id"] == failed_test["asset_id"]

        overdue_items = await client.get("/api/v1/punch-items", params={"overdue": "true"})
        assert overdue_items.json()["total"] == 1
        assert overdue_items.json()["items"][0]["severity"] == "blocking"

        exclusions = await client.get(
            "/api/v1/constraints", params={"kind": "mutual_exclusive_maintenance"}
        )
        assert exclusions.json()["total"] == 1
        assert len(exclusions.json()["items"][0]["asset_ids"]) == 2


async def test_seeded_audit_trail_shows_the_crew_as_actors(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'seed.db').as_posix()}"
    result = seed_demo(database_url)
    settings = Settings(database_url=database_url, docs_enabled=False)
    application = create_app(settings)
    transport = ASGITransport(app=application)
    viewer_auth = {"Authorization": f"Bearer {result.tokens[SEED_VIEWER]}"}
    async with (
        application.router.lifespan_context(application),
        AsyncClient(
            transport=transport, base_url="http://testserver", headers=viewer_auth
        ) as client,
    ):
        engineer_audit = await client.get("/api/v1/audit-entries", params={"actor": SEED_ENGINEER})
        assert engineer_audit.json()["total"] == 54

        reviewer_audit = await client.get("/api/v1/audit-entries", params={"actor": SEED_REVIEWER})
        assert reviewer_audit.json()["total"] == 3

        admin_audit = await client.get("/api/v1/audit-entries", params={"actor": SEED_ADMIN})
        assert admin_audit.json()["total"] == 8
        admin_entities = {entry["entity_type"] for entry in admin_audit.json()["items"]}
        assert admin_entities == {"user", "api_token"}
