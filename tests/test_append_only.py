"""Database-level append-only enforcement on audit and evidence tables.

The four append-only tables (``audit_entries``, ``commissioning_evidence``,
``evidence_attachments``, ``evidence_objects``) were append-only by
convention: the application exposes no update or delete path for them. A
bug, a migration mistake, or anyone holding database credentials could
still rewrite history silently. Migration ``0004`` binds the invariant to
the schema itself with ``BEFORE UPDATE`` / ``BEFORE DELETE`` triggers that
abort the statement.

On SQLite those two triggers leave an insert free to rewrite a row.
``INSERT OR REPLACE`` and ``REPLACE INTO`` resolve a key collision by
deleting the stored row and inserting the new one, and that deletion fires
no delete trigger. Migration ``0005`` adds a ``BEFORE INSERT`` trigger per
table that refuses any insert colliding with a stored row on any of the
table's keys. PostgreSQL has no ``REPLACE``: its upsert and ``MERGE`` take
the update path, which the statement-level trigger from ``0004`` refuses,
and the tests below show that it does.

Triggers are invisible to Alembic autogenerate — the
``test_autogenerate_after_upgrade_head_is_empty`` drift check stays green
precisely because triggers live outside the model metadata it compares.
These tests are the honest replacement for that blindness: they assert the
triggers exist at head and fire on every protected table, so a future
migration that recreates one of these tables (SQLite batch mode drops and
rebuilds the table, silently discarding its triggers) fails CI until it
recreates the triggers too.

These tests follow the session's database: SQLite by default, and the
PostgreSQL server in CI's PostgreSQL job, where they are what proves the
plpgsql trigger actually fires rather than merely that its DDL executed.
The trigger names and the exception class differ between the dialects;
the message an operator would read does not, and neither does the
invariant. The tests that send SQLite's own syntax build their own SQLite
database instead, so they run in every job.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError
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
    MaintenanceOrder,
    PunchItem,
    WorkPermit,
)

# The invariant these tests protect, spelled out rather than imported, so a
# table cannot silently drop out of enforcement by editing the constant.
PROTECTED_TABLES: tuple[str, ...] = (
    "audit_entries",
    "commissioning_evidence",
    "evidence_attachments",
    "evidence_objects",
)

ACTOR_COLUMNS: dict[str, str] = {
    "audit_entries": "actor",
    "commissioning_evidence": "actor",
    "evidence_attachments": "attached_by",
    "evidence_objects": "uploaded_by",
}
"""The column that names who wrote each protected row.

A forged insert below rewrites it, so that a replaced row differs from the
stored one in the value an auditor would read first.
"""

ATTACHMENT_TARGETS: tuple[str, ...] = (
    "commissioning_test_id",
    "punch_item_id",
    "order_id",
    "permit_id",
)
"""The record columns an attachment can hang off, one unique key each."""

REPLACING_INSERTS: tuple[str, ...] = (
    "INSERT OR REPLACE",
    "REPLACE",
    "INSERT OR IGNORE",
)
"""SQLite's inserts that resolve a key collision instead of failing on it.

``REPLACE`` deletes the stored row and inserts the new one in its place;
``IGNORE`` drops the new row in silence. Before migration ``0005`` neither
fired a trigger.
"""

COLLIDING_INSERTS = (
    pytest.param("INSERT", "", id="INSERT"),
    pytest.param("INSERT OR ABORT", "", id="INSERT OR ABORT"),
    pytest.param("INSERT OR FAIL", "", id="INSERT OR FAIL"),
    pytest.param("INSERT OR ROLLBACK", "", id="INSERT OR ROLLBACK"),
    pytest.param("INSERT", " ON CONFLICT DO NOTHING", id="INSERT ON CONFLICT DO NOTHING"),
)
"""The rest of SQLite's ways to send an insert that meets a stored key.

Each is a verb and what follows the copied rows. The first four always
failed, on the key's own constraint, whose message does not say that the
table is append-only. The upsert that does nothing skipped the row in
silence, as ``INSERT OR IGNORE`` did.
"""


def _sqlite_url(tmp_path: Path, name: str = "append-only.db") -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def _migrated_engine(url: str) -> Engine:
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


def _attach_the_object_to_every_kind_of_record(engine: Engine) -> None:
    """Attach the seeded object to a punch item, an order and a permit too.

    With the seeded attachment to the test, every target column then holds
    one attachment of the same object.
    """
    with Session(engine) as session:
        asset = session.scalars(select(Asset)).one()
        stored_object = session.scalars(select(EvidenceObject)).one()
        order = MaintenanceOrder(
            order_type="preventive",
            status="draft",
            asset_id=asset.id,
            title="Quarterly inspection",
            due_date=date(2026, 9, 1),
        )
        punch_item = PunchItem(
            asset_id=asset.id,
            category="defect",
            severity="major",
            status="open",
            title="Fan guard missing",
        )
        session.add_all([order, punch_item])
        session.flush()
        permit = WorkPermit(
            order_id=order.id,
            scope="Work confined to the unit enclosure.",
            status="requested",
            requested_by="ada",
        )
        session.add(permit)
        session.flush()
        session.add_all(
            [
                EvidenceAttachment(
                    evidence_object_id=stored_object.id,
                    punch_item_id=punch_item.id,
                    attached_by="ada",
                ),
                EvidenceAttachment(
                    evidence_object_id=stored_object.id, order_id=order.id, attached_by="ada"
                ),
                EvidenceAttachment(
                    evidence_object_id=stored_object.id, permit_id=permit.id, attached_by="ada"
                ),
            ]
        )
        session.commit()


@pytest.fixture
def seeded(database_url: str) -> Iterator[Engine]:
    """The session's database at head, one row in every protected table."""
    engine = _migrated_engine(database_url)
    try:
        _seed_one_row_per_protected_table(engine)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def seeded_sqlite(tmp_path: Path) -> Iterator[Engine]:
    """A SQLite database at head, one row in every protected table.

    For statements in SQLite's own syntax, which a PostgreSQL server would
    refuse as a syntax error before any trigger could answer.
    """
    engine = _migrated_engine(_sqlite_url(tmp_path))
    try:
        _seed_one_row_per_protected_table(engine)
        yield engine
    finally:
        engine.dispose()


def _trigger_names(engine: Engine) -> set[str]:
    """Every trigger the connected database carries, whichever dialect it is."""
    query = (
        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
        if engine.dialect.name == "postgresql"
        else "SELECT name FROM sqlite_master WHERE type = 'trigger'"
    )
    with engine.connect() as connection:
        return set(connection.execute(text(query)).scalars())


def _expected_trigger_names(engine: Engine) -> set[str]:
    """The trigger names migrations 0004 and 0005 create on this dialect.

    PostgreSQL gets one statement-level trigger per table covering all
    three operations, and nothing for inserts: it has no REPLACE, and its
    upsert and MERGE fire that trigger. SQLite needs one row-level trigger
    per operation because it has no statement-level triggers and no
    TRUNCATE, plus one that refuses an insert colliding with a stored row.
    """
    if engine.dialect.name == "postgresql":
        return {f"trg_{table}_append_only" for table in PROTECTED_TABLES}
    return {
        f"trg_{table}_no_{operation}"
        for table in PROTECTED_TABLES
        for operation in ("update", "delete", "replace")
    }


def _collision_message(table: str) -> str:
    """What the guard from migration 0005 answers, on every table alike."""
    return f"{table} is append-only: an INSERT colliding with an existing row is not allowed"


def _forged_insert(engine: Engine, table: str, verb: str, clause: str = "") -> str:
    """A ``verb`` that copies every stored row of ``table``, actor rewritten.

    Each copy carries every key of the row it copies, so it collides with
    that row on all of them. ``clause`` follows the copy, for the
    ``ON CONFLICT`` of an upsert.
    """
    quote = engine.dialect.identifier_preparer.quote
    names = [column["name"] for column in inspect(engine).get_columns(table)]
    columns = ", ".join(quote(name) for name in names)
    values = ", ".join(
        "'mallory'" if name == ACTOR_COLUMNS[table] else quote(name) for name in names
    )
    # Table and column names come from this file and the schema, never from
    # input. SQLite needs the WHERE to read a following ON CONFLICT as the
    # upsert's rather than as a join constraint.
    return (
        f"{verb} INTO {table} ({columns}) "  # noqa: S608
        f"SELECT {values} FROM {table} WHERE true{clause}"
    )


def _rows(engine: Engine, table: str) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        result = connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))  # noqa: S608
        return [tuple(row) for row in result]


def _assert_refused_and_intact(
    engine: Engine,
    table: str,
    statement: str,
    message: str,
    parameters: dict[str, Any] | None = None,
) -> None:
    """``statement`` fails with ``message`` and ``table`` keeps every row as it was."""
    stored = _rows(engine, table)
    with engine.begin() as connection, pytest.raises(DatabaseError, match=re.escape(message)):
        connection.execute(text(statement), parameters or {})
    assert _rows(engine, table) == stored


def test_the_models_constant_matches_the_protected_tables() -> None:
    """``APPEND_ONLY_TABLES`` and this file must name the same tables."""
    assert APPEND_ONLY_TABLES == PROTECTED_TABLES


@pytest.mark.parametrize("table", PROTECTED_TABLES)
@pytest.mark.parametrize("operation", ("UPDATE", "DELETE"))
def test_a_direct_mutation_is_blocked(database_url: str, table: str, operation: str) -> None:
    """A raw UPDATE or DELETE aborts with the trigger's message, row intact.

    This runs on whichever database the session is configured for, so the
    PostgreSQL job asserts the plpgsql trigger actually fires rather than
    merely that its DDL executed. Both dialects raise the same message —
    SQLite through ``RAISE(ABORT)``, PostgreSQL through ``RAISE EXCEPTION``
    — under different SQLAlchemy exception classes, so the assertion is on
    their common base and on the text the operator would read.
    """
    # Table names come from this file's own constant, never from input.
    statement = (
        f"UPDATE {table} SET id = id"  # noqa: S608
        if operation == "UPDATE"
        else f"DELETE FROM {table}"  # noqa: S608
    )
    engine = _migrated_engine(database_url)
    try:
        _seed_one_row_per_protected_table(engine)
        with (
            engine.begin() as connection,
            pytest.raises(DatabaseError, match=f"{table} is append-only"),
        ):
            connection.execute(text(statement))
        with engine.connect() as connection:
            count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
        assert count == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("statement", REPLACING_INSERTS)
@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_a_replacing_insert_cannot_rewrite_a_protected_row(
    seeded_sqlite: Engine, table: str, statement: str
) -> None:
    """REPLACE and IGNORE meet a stored row and are refused, the row intact.

    Without a guard on inserts, ``REPLACE`` swaps the stored row for the
    forged copy, actor and all, and the update and delete triggers never
    fire; ``IGNORE`` keeps the stored row but answers as though the insert
    had been accepted. Both now fail with the append-only message.
    """
    _assert_refused_and_intact(
        seeded_sqlite,
        table,
        _forged_insert(seeded_sqlite, table, statement),
        _collision_message(table),
    )


@pytest.mark.parametrize(("verb", "clause"), COLLIDING_INSERTS)
@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_a_colliding_insert_is_refused_with_the_append_only_message(
    seeded_sqlite: Engine, table: str, verb: str, clause: str
) -> None:
    """Every other insert that meets a stored row is refused the same way.

    A plain insert with a stored key always failed, but on the key's own
    constraint; the refusal now says what the table is, whichever conflict
    clause the statement carries.
    """
    _assert_refused_and_intact(
        seeded_sqlite,
        table,
        _forged_insert(seeded_sqlite, table, verb, clause),
        _collision_message(table),
    )


def test_evidence_objects_cannot_be_replaced_through_their_hash(seeded_sqlite: Engine) -> None:
    """A new id cannot take over the content hash of a stored object.

    ``sha256`` is the object's second key. REPLACE resolves a collision on
    it as on the id: the stored object, the row every attachment of those
    bytes points at, is deleted, and the forged row takes its hash.
    """
    statement = (
        "INSERT OR REPLACE INTO evidence_objects (id, sha256, size_bytes, filename,"
        " content_type, uploaded_by, created_at) SELECT 'forged-object', sha256, size_bytes,"
        " 'forged.pdf', content_type, 'mallory', created_at FROM evidence_objects"
    )
    _assert_refused_and_intact(
        seeded_sqlite, "evidence_objects", statement, _collision_message("evidence_objects")
    )


def test_evidence_objects_cannot_be_replaced_through_their_rowid(seeded_sqlite: Engine) -> None:
    """The rowid SQLite keeps beside the text id is a key REPLACE honours.

    ``evidence_objects`` is keyed by a text id, so it also has a hidden
    integer rowid, and an insert may name it. A row with a new id and a new
    hash, inserted under a stored object's rowid, replaces that object.
    """
    statement = (
        "INSERT OR REPLACE INTO evidence_objects (rowid, id, sha256, size_bytes, filename,"
        " content_type, uploaded_by, created_at) SELECT rowid, 'forged-object', :forged_hash,"
        " size_bytes, 'forged.pdf', content_type, 'mallory', created_at FROM evidence_objects"
    )
    _assert_refused_and_intact(
        seeded_sqlite,
        "evidence_objects",
        statement,
        _collision_message("evidence_objects"),
        {"forged_hash": "f" * 64},
    )


def test_a_row_stored_under_rowid_minus_one_stays_protected(seeded_sqlite: Engine) -> None:
    """A row whose rowid is -1 is guarded like any other, at a price this pins.

    SQLite shows a ``BEFORE INSERT`` trigger -1 for a rowid it has yet to
    assign. Exempting -1 from the guard would let REPLACE rewrite a row
    stored under it, so the guard does not, and while such a row exists
    every insert that leaves the id to SQLite collides with it. Only an
    explicit insert can store that row; the application never names an
    integer id.
    """
    stored = (
        "INSERT INTO audit_entries (id, occurred_at, actor, entity_type, entity_id, action)"
        " VALUES (-1, '2026-01-01 00:00:00', 'ada', 'asset', 'a-1', 'updated')"
    )
    with seeded_sqlite.begin() as connection:
        connection.execute(text(stored))
    message = _collision_message("audit_entries")
    _assert_refused_and_intact(
        seeded_sqlite,
        "audit_entries",
        "INSERT OR REPLACE INTO audit_entries (id, occurred_at, actor, entity_type, entity_id,"
        " action) VALUES (-1, '2026-01-01 00:00:00', 'mallory', 'asset', 'a-1', 'deleted')",
        message,
    )
    _assert_refused_and_intact(
        seeded_sqlite,
        "audit_entries",
        "INSERT INTO audit_entries (occurred_at, actor, entity_type, entity_id, action)"
        " VALUES ('2026-01-02 00:00:00', 'ben', 'asset', 'a-1', 'updated')",
        message,
    )


@pytest.mark.parametrize("target", ATTACHMENT_TARGETS)
def test_an_attachment_cannot_be_replaced_through_its_target(tmp_path: Path, target: str) -> None:
    """An object attaches to a record once, and a second attach cannot replace the first.

    Each pair of object and record is a unique key. REPLACE resolves a new
    row on that pair by deleting the stored attachment, whatever id the new
    row is given; this one leaves the id to SQLite.
    """
    engine = _migrated_engine(_sqlite_url(tmp_path))
    try:
        _seed_one_row_per_protected_table(engine)
        _attach_the_object_to_every_kind_of_record(engine)
        # The target column comes from this file's own constant.
        statement = (
            f"INSERT OR REPLACE INTO evidence_attachments (evidence_object_id, {target},"  # noqa: S608
            f" attached_by, attached_at) SELECT evidence_object_id, {target}, 'mallory',"
            f" attached_at FROM evidence_attachments WHERE {target} IS NOT NULL"
        )
        _assert_refused_and_intact(
            engine, "evidence_attachments", statement, _collision_message("evidence_attachments")
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_an_upsert_cannot_rewrite_a_protected_row(seeded: Engine, table: str) -> None:
    """``INSERT … ON CONFLICT DO UPDATE`` is refused on both dialects, row intact.

    The statement is the same on both. SQLite fires the update trigger for
    the upsert's update, or the insert guard before it where there is one.
    PostgreSQL fires its statement-level update trigger before the
    statement reaches a row, which is the refusal the PostgreSQL job checks
    word for word.
    """
    actor = ACTOR_COLUMNS[table]
    message = f"{table} is append-only"
    if seeded.dialect.name == "postgresql":
        message += ": UPDATE is not allowed"
    _assert_refused_and_intact(
        seeded,
        table,
        _forged_insert(
            seeded, table, "INSERT", f" ON CONFLICT (id) DO UPDATE SET {actor} = excluded.{actor}"
        ),
        message,
    )


@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_merge_cannot_rewrite_a_protected_row(database_url: str, table: str) -> None:
    """``MERGE … WHEN MATCHED THEN UPDATE`` is refused by the statement trigger.

    MERGE fires the statement-level triggers of every action it names before
    it reads a row, so the update trigger refuses it whether or not a row
    matches. MERGE needs PostgreSQL 15 or newer; CI runs 16. SQLite has no
    MERGE, so there is nothing to send it there.
    """
    if make_url(database_url).get_backend_name() != "postgresql":
        pytest.skip("MERGE is PostgreSQL syntax; this session's database is SQLite")
    engine = _migrated_engine(database_url)
    try:
        _seed_one_row_per_protected_table(engine)
        # Table and column names come from this file's own constants.
        statement = (
            f"MERGE INTO {table} AS stored USING (SELECT id FROM {table}) AS forged"  # noqa: S608
            f" ON stored.id = forged.id"
            f" WHEN MATCHED THEN UPDATE SET {ACTOR_COLUMNS[table]} = 'mallory'"
        )
        _assert_refused_and_intact(
            engine, table, statement, f"{table} is append-only: UPDATE is not allowed"
        )
    finally:
        engine.dispose()


def test_ordinary_inserts_still_pass(seeded: Engine) -> None:
    """An insert that collides with nothing goes through, key named or not.

    The application leaves integer ids to the database and generates the
    text ids itself, so its inserts never meet a stored key. The seeded
    object is attached again to a second test and to an order: a NULL
    target column on either side matches nothing, as in the unique
    constraints the guard mirrors.
    """
    with Session(seeded) as session:
        test = session.scalars(select(CommissioningTest)).one()
        stored_object = session.scalars(select(EvidenceObject)).one()
        second_test = CommissioningTest(
            asset_id=test.asset_id,
            title="Black start",
            planned_date=date(2026, 9, 2),
            status="pending",
        )
        order = MaintenanceOrder(
            order_type="preventive",
            status="draft",
            asset_id=test.asset_id,
            title="Quarterly inspection",
            due_date=date(2026, 9, 1),
        )
        new_object = EvidenceObject(
            sha256="1" * 64,
            size_bytes=4,
            filename="photo.jpg",
            content_type="image/jpeg",
            uploaded_by="ben",
        )
        session.add_all([second_test, order, new_object])
        session.flush()
        session.add_all(
            [
                AuditEntry(
                    actor="ben",
                    entity_type="asset",
                    entity_id=test.asset_id,
                    action="updated",
                    scope="installation",
                    before={"name": "UPS under test"},
                    after={"name": "UPS 1"},
                ),
                CommissioningEvidence(test_id=test.id, note="Second reading nominal.", actor="ben"),
                EvidenceAttachment(
                    evidence_object_id=stored_object.id,
                    commissioning_test_id=second_test.id,
                    attached_by="ben",
                ),
                EvidenceAttachment(
                    evidence_object_id=stored_object.id, order_id=order.id, attached_by="ben"
                ),
                EvidenceAttachment(
                    evidence_object_id=new_object.id,
                    commissioning_test_id=test.id,
                    attached_by="ben",
                ),
            ]
        )
        session.commit()
    with seeded.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_entries (id, occurred_at, actor, entity_type, entity_id,"
                " action, scope) VALUES (1000, '2026-01-01 00:00:00', 'ben', 'asset', 'a-1',"
                " 'updated', 'installation')"
            )
        )
    with seeded.connect() as connection:
        counts = {
            table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
            for table in PROTECTED_TABLES
        }
    assert counts == {
        "audit_entries": 3,
        "commissioning_evidence": 2,
        "evidence_attachments": 4,
        "evidence_objects": 2,
    }


def test_triggers_exist_after_upgrade_head(database_url: str) -> None:
    """Every protected table carries all of its append-only triggers at head.

    This presence check is what keeps the autogenerate drift test honest:
    autogenerate cannot see triggers, so a migration that dropped them —
    including a batch table rebuild that discards them as a side effect —
    would pass every schema comparison and fail only here.
    """
    engine = _migrated_engine(database_url)
    try:
        assert _expected_trigger_names(engine) <= _trigger_names(engine)
    finally:
        engine.dispose()


def test_downgrading_below_0005_keeps_the_update_and_delete_triggers(tmp_path: Path) -> None:
    """Below ``0005`` the insert guards are gone and ``0004``'s triggers remain."""
    url = _sqlite_url(tmp_path, "downgrade-0005.db")
    upgrade_to_head(url)
    command.downgrade(build_alembic_config(url), "0004")
    engine = build_engine(url)
    try:
        assert _trigger_names(engine) == {
            f"trg_{table}_no_{operation}"
            for table in PROTECTED_TABLES
            for operation in ("update", "delete")
        }
    finally:
        engine.dispose()


def test_downgrade_removes_the_triggers_and_the_enforcement(tmp_path: Path) -> None:
    """Downgrading below 0004 returns the tables to convention-only.

    This one builds its own SQLite URL: it asserts on raw literals a
    PostgreSQL server would type differently, and the downgrade path's
    PostgreSQL branch is covered by the migration chain CI runs there.
    """
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
