"""The migration chain must reproduce exactly the schema the models declare.

The comparisons run on SQLite, the same backend the rest of the suite uses.
Normalisations applied before comparing (each is benign):

- the ``alembic_version`` bookkeeping table is excluded;
- SQLite's internal ``sqlite_autoindex_*`` indexes are excluded;
- unique constraints compare by their column tuple, because SQLite does not
  preserve names for constraints declared inline on a column;
- foreign keys compare by (referred table, columns, referred columns,
  ondelete) since SQLite reports no constraint names for them.

Everything else — table set, column names, compiled column types,
nullability, server defaults, primary keys, and named indexes — must match
exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, text

from cfm.config import Settings
from cfm.database import Base, build_engine
from cfm.main import create_app
from cfm.migrate import (
    SchemaOutOfDateError,
    build_alembic_config,
    current_revision,
    ensure_fresh_schema,
    head_revision,
    upgrade_to_head,
)


def _sqlite_url(tmp_path: Path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def _schema_snapshot(engine: Engine) -> dict[str, Any]:
    """The inspected schema as plain comparable data."""
    inspector = inspect(engine)
    snapshot: dict[str, Any] = {}
    for table in sorted(inspector.get_table_names()):
        if table == "alembic_version":
            continue
        columns = {
            column["name"]: (
                column["type"].compile(engine.dialect),
                column["nullable"],
                column["default"],
            )
            for column in inspector.get_columns(table)
        }
        primary_key = tuple(inspector.get_pk_constraint(table)["constrained_columns"])
        foreign_keys = sorted(
            (
                foreign_key["referred_table"],
                tuple(foreign_key["constrained_columns"]),
                tuple(foreign_key["referred_columns"]),
                (foreign_key["options"] or {}).get("ondelete"),
            )
            for foreign_key in inspector.get_foreign_keys(table)
        )
        uniques = sorted(
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table)
        )
        indexes = sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table)
            if not (index["name"] or "").startswith("sqlite_autoindex")
        )
        snapshot[table] = {
            "columns": columns,
            "primary_key": primary_key,
            "foreign_keys": foreign_keys,
            "uniques": uniques,
            "indexes": indexes,
        }
    return snapshot


def test_upgrade_head_matches_the_create_all_schema(tmp_path: Path) -> None:
    """A fresh database migrated to head equals what the models declare."""
    reference_engine = build_engine(_sqlite_url(tmp_path, "via-create-all.db"))
    try:
        Base.metadata.create_all(reference_engine)
        expected = _schema_snapshot(reference_engine)
    finally:
        reference_engine.dispose()

    migrated_url = _sqlite_url(tmp_path, "via-migrations.db")
    upgrade_to_head(migrated_url)
    migrated_engine = build_engine(migrated_url)
    try:
        actual = _schema_snapshot(migrated_engine)
    finally:
        migrated_engine.dispose()

    assert expected, "create_all produced no tables; the reference side is broken"
    assert actual == expected


def test_autogenerate_after_upgrade_head_is_empty(tmp_path: Path) -> None:
    """Autogenerate against a migrated database detects no drift."""
    url = _sqlite_url(tmp_path, "autogenerate.db")
    upgrade_to_head(url)
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            migration_context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            diff = compare_metadata(migration_context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


def test_upgrade_head_stamps_the_head_revision(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "stamped.db")
    upgrade_to_head(url)
    assert head_revision() is not None
    assert current_revision(url) == head_revision()


@pytest.mark.anyio
async def test_startup_refuses_an_empty_unmigrated_database(tmp_path: Path) -> None:
    """Without auto-upgrade the application must not invent a schema."""
    settings = Settings(
        database_url=_sqlite_url(tmp_path, "empty.db"),
        docs_enabled=False,
        db_auto_upgrade=False,
    )
    application = create_app(settings)
    with pytest.raises(SchemaOutOfDateError, match="alembic upgrade head"):
        async with application.router.lifespan_context(application):
            pass  # pragma: no cover - startup must fail before this point


@pytest.mark.anyio
async def test_startup_refuses_a_pre_migration_schema(tmp_path: Path) -> None:
    """A create_all database without a stamp gets the adoption message."""
    url = _sqlite_url(tmp_path, "legacy.db")
    engine = build_engine(url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    settings = Settings(database_url=url, docs_enabled=False, db_auto_upgrade=False)
    application = create_app(settings)
    with pytest.raises(SchemaOutOfDateError, match="alembic stamp 0001"):
        async with application.router.lifespan_context(application):
            pass  # pragma: no cover - startup must fail before this point


@pytest.mark.anyio
async def test_startup_auto_upgrades_when_enabled(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "auto.db")
    settings = Settings(database_url=url, docs_enabled=False, db_auto_upgrade=True)
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        assert current_revision(url) == head_revision()


@pytest.mark.anyio
async def test_startup_accepts_a_migrated_database(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "migrated.db")
    upgrade_to_head(url)
    settings = Settings(database_url=url, docs_enabled=False, db_auto_upgrade=False)
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        pass


def test_ensure_fresh_schema_migrates_an_empty_database(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path, "fresh.db")
    ensure_fresh_schema(url)
    assert current_revision(url) == head_revision()


def test_upgrade_from_0001_carries_users_to_installation_scope(tmp_path: Path) -> None:
    """Users that existed before 0002 keep installation-wide write authority.

    The pre-0002 data is created against the real 0001 schema, then migrated
    forward; every user must come out holding exactly one installation-wide
    site grant, so existing tokens behave exactly as they did before.
    """
    url = _sqlite_url(tmp_path, "carry.db")
    config = build_alembic_config(url)
    command.upgrade(config, "0001")

    engine = build_engine(url)
    try:
        with engine.begin() as connection:
            for user_id, username, role in (
                ("user-a", "ada-eng", "engineer"),
                ("user-b", "ben-admin", "admin"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO users (id, username, display_name, role, is_active,"
                        " created_at, updated_at) VALUES (:id, :username, :name, :role, 1,"
                        " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                    ),
                    {"id": user_id, "username": username, "name": username, "role": role},
                )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT user_id, site_id FROM site_grants ORDER BY user_id")
            ).all()
        columns = {column["name"] for column in inspect(engine).get_columns("audit_entries")}
    finally:
        engine.dispose()
    assert rows == [("user-a", None), ("user-b", None)]
    assert "scope" in columns


def test_ensure_fresh_schema_refuses_a_pre_migration_database(tmp_path: Path) -> None:
    """Migrating over unstamped tables would fail halfway; refuse instead."""
    url = _sqlite_url(tmp_path, "legacy-fresh.db")
    engine = build_engine(url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    with pytest.raises(SchemaOutOfDateError, match="alembic stamp 0001"):
        ensure_fresh_schema(url)
