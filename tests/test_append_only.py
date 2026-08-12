"""Database-level append-only enforcement on audit and evidence tables.

The four append-only tables (``audit_entries``, ``commissioning_evidence``,
``evidence_attachments``, ``evidence_objects``) were append-only by
convention: the application exposes no update or delete path for them. A
bug, a migration mistake, or anyone holding database credentials could
still rewrite history silently. Migration ``0004`` binds the invariant to
the schema itself with ``BEFORE UPDATE`` / ``BEFORE DELETE`` triggers that
abort the statement.

Triggers are invisible to Alembic autogenerate — the
``test_autogenerate_after_upgrade_head_is_empty`` drift check stays green
precisely because triggers live outside the model metadata it compares.
These tests are the honest replacement for that blindness: they assert the
triggers exist at head and fire on every protected table, so a future
migration that recreates one of these tables (SQLite batch mode drops and
rebuilds the table, silently discarding its triggers) fails CI until it
recreates the triggers too.

Only the SQLite trigger DDL runs here, because CI has no PostgreSQL
container; the PostgreSQL branch of migration ``0004`` is deliberately
trivial and reviewed by inspection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cfm.database import build_engine
from cfm.migrate import build_alembic_config, upgrade_to_head
from cfm.models import (
    APPEND_ONLY_TABLES,
    Asset,
    AuditEntry,
    CommissioningEvidence,
    CommissioningTest,
    EvidenceAttachment,
    EvidenceObject,
    Location,
)

# The invariant these tests protect, spelled out rather than imported, so a
# table cannot silently drop out of enforcement by editing the constant.
PROTECTED_TABLES: tuple[str, ...] = (
    "audit_entries",
    "commissioning_evidence",
    "evidence_attachments",
    "evidence_objects",
)


def _migrated_engine(tmp_path: Path) -> Engine:
    url = f"sqlite:///{(tmp_path / 'append-only.db').as_posix()}"
    upgrade_to_head(url)
    return build_engine(url)


def _seed_one_row_per_protected_table(engine: Engine) -> None:
    """Insert a minimal object graph so every protected table has one row."""
    with Session(engine) as session:
        site = Location(kind="site", code="CAMP-T", name="Trigger Campus")
        session.add(site)
        session.flush()
        asset = Asset(
            tag="UPS-T-01",
            name="UPS under test",
            asset_type="ups",
            criticality="critical",
            status="installed",
            location_id=site.id,
            site_id=site.id,
        )
        session.add(asset)
        session.flush()
        test = CommissioningTest(
            asset_id=asset.id,
            title="Load transfer",
            planned_date=datetime.now(UTC).date(),
            status="pending",
        )
        session.add(test)
        session.flush()
        session.add(CommissioningEvidence(test_id=test.id, note="Reading nominal.", actor="ada"))
        evidence_object = EvidenceObject(
            sha256="0" * 64,
            size_bytes=4,
            filename="report.pdf",
            content_type="application/pdf",
            uploaded_by="ada",
        )
        session.add(evidence_object)
        session.flush()
        session.add(
            EvidenceAttachment(
                evidence_object_id=evidence_object.id,
                commissioning_test_id=test.id,
                attached_by="ada",
            )
        )
        session.add(
            AuditEntry(
                actor="ada",
                entity_type="asset",
                entity_id=asset.id,
                action="created",
                scope="installation",
                before=None,
                after={"tag": "UPS-T-01"},
            )
        )
        session.commit()


def _trigger_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        ).scalars()
        return set(rows)


def test_the_models_constant_matches_the_protected_tables() -> None:
    """``APPEND_ONLY_TABLES`` and this file must name the same tables."""
    assert APPEND_ONLY_TABLES == PROTECTED_TABLES


@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_direct_update_is_blocked(tmp_path: Path, table: str) -> None:
    """A raw UPDATE against a protected table aborts with the trigger message."""
    engine = _migrated_engine(tmp_path)
    try:
        _seed_one_row_per_protected_table(engine)
        with (
            engine.begin() as connection,
            pytest.raises(IntegrityError, match=f"{table} is append-only"),
        ):
            connection.execute(text(f"UPDATE {table} SET id = id"))  # noqa: S608
        with engine.connect() as connection:
            count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
        assert count == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_direct_delete_is_blocked(tmp_path: Path, table: str) -> None:
    """A raw DELETE against a protected table aborts and the row survives."""
    engine = _migrated_engine(tmp_path)
    try:
        _seed_one_row_per_protected_table(engine)
        with (
            engine.begin() as connection,
            pytest.raises(IntegrityError, match=f"{table} is append-only"),
        ):
            connection.execute(text(f"DELETE FROM {table}"))  # noqa: S608
        with engine.connect() as connection:
            count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
        assert count == 1
    finally:
        engine.dispose()


def test_triggers_exist_after_upgrade_head(tmp_path: Path) -> None:
    """Every protected table carries its UPDATE and DELETE trigger at head.

    This presence check is what keeps the autogenerate drift test honest:
    autogenerate cannot see triggers, so a migration that dropped them —
    including a batch table rebuild that discards them as a side effect —
    would pass every schema comparison and fail only here.
    """
    engine = _migrated_engine(tmp_path)
    try:
        names = _trigger_names(engine)
    finally:
        engine.dispose()
    expected = {
        f"trg_{table}_no_{operation}"
        for table in PROTECTED_TABLES
        for operation in ("update", "delete")
    }
    assert expected <= names


def test_downgrade_removes_the_triggers_and_the_enforcement(tmp_path: Path) -> None:
    """Downgrading below 0004 returns the tables to convention-only."""
    url = f"sqlite:///{(tmp_path / 'downgrade.db').as_posix()}"
    upgrade_to_head(url)
    command.downgrade(build_alembic_config(url), "0003")
    engine = build_engine(url)
    try:
        assert not any(name.startswith("trg_") for name in _trigger_names(engine))
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO evidence_objects (id, sha256, size_bytes, filename,"
                    " content_type, uploaded_by, created_at) VALUES ('obj-1', :sha, 4,"
                    " 'report.pdf', 'application/pdf', 'ada', '2026-01-01 00:00:00')"
                ),
                {"sha": "1" * 64},
            )
            connection.execute(text("UPDATE evidence_objects SET filename = 'renamed.pdf'"))
            connection.execute(text("DELETE FROM evidence_objects"))
    finally:
        engine.dispose()
