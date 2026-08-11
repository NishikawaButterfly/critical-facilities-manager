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

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect

from cfm.database import Base, build_engine
from cfm.migrate import current_revision, head_revision, upgrade_to_head


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
