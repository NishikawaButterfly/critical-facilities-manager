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

The tests at the end of the module are about what a run of the chain
guarantees rather than what it builds: a run is one transaction, so a
failure anywhere in it leaves the schema and the revision stamp where the
run started. Those that take ``database_url`` follow the session's database,
SQLite by default and the PostgreSQL server in CI's PostgreSQL job, because
the guarantee has to hold on both. Those that need a failure inject a
failing revision after head into a copy of the packaged scripts, so the
``env.py`` under test is always the one in this tree.
"""

from __future__ import annotations

import io
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.util import CommandError
from sqlalchemy import Connection, Engine, create_engine, event, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from cfm.config import Settings
from cfm.database import Base, build_engine
from cfm.main import create_app
from cfm.migrate import (
    SCRIPTS_PATH,
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
    """Migrating over unstamped tables would fail at the first of them; refuse instead."""
    url = _sqlite_url(tmp_path, "legacy-fresh.db")
    engine = build_engine(url)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    with pytest.raises(SchemaOutOfDateError, match="alembic stamp 0001"):
        ensure_fresh_schema(url)


# --- A run of the chain is one transaction ---------------------------------

INJECTED_REVISION = "injected"

_INJECTED_SCRIPT = '''"""Injected after head by tests/test_migrations.py.

Revision ID: {revision}
Revises: {down_revision}
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = {revision!r}
down_revision = {down_revision!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
{body}


def downgrade() -> None:
    pass
'''

_ADD_A_COLUMN = 'op.add_column("audit_entries", sa.Column("injected_note", sa.Text()))'
_CREATE_A_TABLE = (
    'op.create_table("injected_table", sa.Column("id", sa.Integer(), primary_key=True))'
)
_RAISE = 'raise RuntimeError("injected failure after DDL")'
_INSERT_A_ROW = 'op.execute("INSERT INTO injected_table (id) VALUES (1)")'
_CREATE_A_SECOND_TABLE = (
    'op.create_table("injected_after", sa.Column("id", sa.Integer(), primary_key=True))'
)
_CATCH_AND_CARRY_ON = ("except sa.exc.DBAPIError:", "    pass")
# On SQLite each of these ends the whole transaction, not only the statement
# that failed. On PostgreSQL each is a syntax error, which aborts the
# transaction there.
_INSERT_OR_ROLLBACK = (
    "try:",
    '    op.execute("INSERT OR ROLLBACK INTO injected_table (id) VALUES (1)")',
    *_CATCH_AND_CARRY_ON,
)
_RAISE_ROLLBACK_IN_A_TRIGGER = (
    "try:",
    '    op.execute("CREATE TRIGGER injected_guard BEFORE INSERT ON injected_table'
    " BEGIN SELECT RAISE(ROLLBACK, 'injected'); END\")",
    '    op.execute("INSERT INTO injected_table (id) VALUES (2)")',
    *_CATCH_AND_CARRY_ON,
)
# A second connection asks for the write lock without waiting, and the
# revision fails if it gets it.
_ASK_FOR_THE_WRITE_LOCK = (
    "import sqlite3",
    "other = sqlite3.connect(op.get_bind().engine.url.database, timeout=0)",
    "try:",
    '    other.execute("BEGIN IMMEDIATE")',
    "except sqlite3.OperationalError as error:",
    '    if "database is locked" not in str(error):',
    "        raise",
    "else:",
    '    raise RuntimeError("a second connection got the write lock during the run")',
    "finally:",
    "    other.close()",
)


def _scripts_with_a_revision_after_head(tmp_path: Path, statements: tuple[str, ...]) -> Path:
    """A copy of the packaged scripts, ``env.py`` included, plus one revision after head."""
    scripts = tmp_path / "migrations"
    shutil.copytree(SCRIPTS_PATH, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    script = _INJECTED_SCRIPT.format(
        revision=INJECTED_REVISION,
        down_revision=head_revision(),
        body="\n".join(f"    {statement}" for statement in statements),
    )
    (scripts / "versions" / f"{INJECTED_REVISION}.py").write_text(script, encoding="utf-8")
    return scripts


def _injected_config(scripts: Path, url: str) -> Config:
    config = build_alembic_config(url)
    config.set_main_option("script_location", str(scripts))
    return config


def _table_names(url: str) -> list[str]:
    engine = build_engine(url)
    try:
        return inspect(engine).get_table_names()
    finally:
        engine.dispose()


@dataclass(frozen=True)
class _DatabaseState:
    stamp: str | None
    schema: dict[str, Any]
    triggers: frozenset[str]
    rows: dict[str, int]


def _database_state(url: str) -> _DatabaseState:
    """Stamp, schema, triggers and row counts: everything a failed run must leave alone."""
    engine = build_engine(url)
    try:
        schema = _schema_snapshot(engine)
        trigger_query = (
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
            if engine.dialect.name == "postgresql"
            else "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
        with engine.connect() as connection:
            triggers = frozenset(connection.execute(text(trigger_query)).scalars())
            rows = {
                # Table names come from the inspected schema, never from input.
                table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()  # noqa: S608
                for table in schema
            }
    finally:
        engine.dispose()
    return _DatabaseState(current_revision(url), schema, triggers, rows)


def _seed_a_site(url: str) -> None:
    """One location row whose parent_id is NULL, as every site's is."""
    engine = build_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO locations (id, kind, code, name, parent_id, created_at,"
                    " updated_at) VALUES ('site-a', 'site', 'CAMP-A', 'Campus A', NULL,"
                    " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                )
            )
    finally:
        engine.dispose()


def _transactional_engine(url: str) -> Engine:
    """An engine whose transactions are real ones on either database.

    PostgreSQL connections already behave so. On SQLite this is the recipe in
    SQLAlchemy's SQLite dialect documentation: the sqlite3 driver is told to
    issue no BEGIN of its own, and the engine emits BEGIN whenever a
    transaction starts.
    """
    engine = build_engine(url)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _leave_transactions_to_sqlalchemy(dbapi_connection: Any, _record: Any) -> None:
            dbapi_connection.isolation_level = None

        @event.listens_for(engine, "begin")
        def _begin(connection: Connection) -> None:
            connection.exec_driver_sql("BEGIN")

    return engine


@pytest.mark.parametrize(
    ("statements", "error", "message"),
    [
        pytest.param(
            (_ADD_A_COLUMN, _CREATE_A_TABLE, _RAISE),
            RuntimeError,
            "injected failure after DDL",
            id="after-ddl",
        ),
        pytest.param(
            (
                _CREATE_A_TABLE,
                'with op.batch_alter_table("locations") as batch_op:',
                '    batch_op.alter_column("parent_id", existing_type=sa.String(36),'
                " nullable=False)",
            ),
            IntegrityError,
            "parent_id",
            id="inside-a-batch-rebuild",
        ),
        pytest.param(
            (_CREATE_A_TABLE, 'op.execute("DELETE FROM alembic_version")'),
            CommandError,
            "expected to match one row",
            id="while-stamping",
        ),
    ],
)
def test_a_failing_revision_leaves_the_schema_where_the_run_started(
    database_url: str,
    tmp_path: Path,
    statements: tuple[str, ...],
    error: type[Exception],
    message: str,
) -> None:
    """A revision that fails takes everything it did with it, and the stamp stays.

    Three places a revision can fail once it has issued DDL: in its own code;
    inside a batch rebuild, which on SQLite creates a copy of the table, fills
    it and swaps it in, refused here by a row the tightened column cannot
    hold; and while the new revision is being recorded, here because the
    revision removed the stamp row that recording updates. Each time the
    database must come out exactly as the run found it: the same tables,
    columns, indexes and constraints, the same triggers, the same number of
    rows in every table and the same stamp. On SQLite the DDL a revision
    issued before its first statement that changed data used to be kept, so
    a second attempt failed on what the first had left behind.
    """
    start = head_revision()
    assert start is not None
    config = _injected_config(
        _scripts_with_a_revision_after_head(tmp_path, statements), database_url
    )
    command.upgrade(config, start)
    _seed_a_site(database_url)
    before = _database_state(database_url)

    with pytest.raises(error, match=message):
        command.upgrade(config, "head")

    after = _database_state(database_url)
    left_behind = sorted(set(after.schema) - set(before.schema))
    assert not left_behind, f"the failed run left tables behind: {left_behind}"
    assert after == before


@pytest.mark.parametrize("handed", [False, True], ids=["own-connection", "handed-connection"])
def test_a_failing_run_rolls_back_every_revision_it_applied(
    database_url: str, tmp_path: Path, handed: bool
) -> None:
    """A run from an empty database that fails at its last revision leaves it empty.

    No stamp and no tables: the revisions that succeeded are undone with the
    one that failed, whether the migrations open their own connection or are
    handed one whose transactions are real. SQLite used to commit after each
    revision even on such a connection, so the database came out stamped at
    the last revision that had succeeded.
    """
    scripts = _scripts_with_a_revision_after_head(
        tmp_path, (_ADD_A_COLUMN, _CREATE_A_TABLE, _RAISE)
    )
    config = _injected_config(scripts, database_url)
    with ExitStack() as stack:
        if handed:
            engine = _transactional_engine(database_url)
            stack.callback(engine.dispose)
            config.attributes["connection"] = stack.enter_context(engine.connect())
        with pytest.raises(RuntimeError, match="injected failure after DDL"):
            command.upgrade(config, "head")

    assert current_revision(database_url) is None
    assert _table_names(database_url) == []


@pytest.mark.parametrize(
    "statements",
    [
        pytest.param(
            (_CREATE_A_TABLE, _INSERT_A_ROW, *_INSERT_OR_ROLLBACK, _CREATE_A_SECOND_TABLE),
            id="insert-or-rollback",
        ),
        pytest.param(
            (_CREATE_A_TABLE, *_RAISE_ROLLBACK_IN_A_TRIGGER, _CREATE_A_SECOND_TABLE),
            id="raise-rollback-in-a-trigger",
        ),
        pytest.param(
            (_CREATE_A_TABLE, _INSERT_A_ROW, *_INSERT_OR_ROLLBACK),
            id="insert-or-rollback-before-the-stamp",
        ),
    ],
)
def test_a_run_stops_at_the_first_statement_after_its_transaction_ended(
    database_url: str, tmp_path: Path, statements: tuple[str, ...]
) -> None:
    """A revision that carries on after its transaction ended leaves nothing behind.

    After some errors SQLite rolls back the whole transaction rather than
    the statement: a conflict resolved with ROLLBACK, or RAISE(ROLLBACK) in
    a trigger. A revision that caught such an error and went on used to run
    every later statement outside any transaction, each committed on its
    own, the new stamp included, so the run could even report success with
    the work before the error gone. PostgreSQL refuses every statement after
    an error until the transaction ends, and on SQLite the migration
    environment refuses the next statement itself. Either way the run fails
    at the first statement after the error, whether that is the revision's
    own or the stamp, and the database is exactly as the run found it.
    """
    start = head_revision()
    assert start is not None
    config = _injected_config(
        _scripts_with_a_revision_after_head(tmp_path, statements), database_url
    )
    command.upgrade(config, start)
    _seed_a_site(database_url)
    before = _database_state(database_url)

    error, message = (
        (CommandError, "transaction ended before the run did")
        if database_url.startswith("sqlite")
        else (DBAPIError, "current transaction is aborted")
    )
    with pytest.raises(error, match=message):
        command.upgrade(config, "head")

    assert _database_state(database_url) == before


def test_a_revision_that_rolls_back_to_a_savepoint_carries_on(
    database_url: str, tmp_path: Path
) -> None:
    """An error caught inside a savepoint leaves the run's transaction open.

    Only the savepoint is rolled back, so the statements after it run in the
    same transaction and the run commits, on both databases: what stops a
    run is a transaction that has ended, not an error a revision handled.
    """
    statements = (
        _CREATE_A_TABLE,
        _INSERT_A_ROW,
        "try:",
        "    with op.get_bind().begin_nested():",
        f"        {_INSERT_A_ROW}",
        "except sa.exc.IntegrityError:",
        "    pass",
        _CREATE_A_SECOND_TABLE,
    )
    config = _injected_config(
        _scripts_with_a_revision_after_head(tmp_path, statements), database_url
    )
    command.upgrade(config, "head")

    state = _database_state(database_url)
    assert state.stamp == INJECTED_REVISION
    assert state.rows["injected_table"] == 1
    assert "injected_after" in state.schema


def test_an_autocommit_block_still_commits_part_of_a_run(tmp_path: Path) -> None:
    """Alembic's autocommit_block() ends the run's transaction on purpose, and still works.

    It commits what the run did before the block and begins a new
    transaction after it, so the refusal of statements issued once a
    transaction has ended must let through the BEGIN that opens the next one.
    """
    url = _sqlite_url(tmp_path, "autocommit-block.db")
    statements = (
        _CREATE_A_TABLE,
        "with op.get_context().autocommit_block():",
        '    op.execute("CREATE TABLE injected_inside (id INTEGER PRIMARY KEY)")',
        _CREATE_A_SECOND_TABLE,
    )
    config = _injected_config(_scripts_with_a_revision_after_head(tmp_path, statements), url)
    command.upgrade(config, "head")

    assert current_revision(url) == INJECTED_REVISION
    assert {"injected_table", "injected_inside", "injected_after"} <= set(_table_names(url))


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(command.current, id="current"),
        pytest.param(command.check, id="check"),
        pytest.param(lambda config: command.upgrade(config, "head"), id="upgrade"),
    ],
)
def test_every_command_takes_the_write_lock_before_it_reads(
    tmp_path: Path, run: Callable[[Config], None]
) -> None:
    """Every command that opens an SQLite database takes its write lock before reading it.

    The run begins with BEGIN IMMEDIATE, so while another connection holds
    the lock, a command stops before it reads the stamp, even one that would
    change nothing: current, check, or an upgrade with nothing left to do. A
    plain BEGIN would let each of them read at once, and two upgrades started
    together could both read the same stamp. timeout=0 in the URL makes the
    command stop at once instead of after the driver's five-second wait.
    """
    url = _sqlite_url(tmp_path, "locked.db")
    upgrade_to_head(url)
    other = sqlite3.connect(tmp_path / "locked.db", timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")
        with pytest.raises(OperationalError, match="database is locked"):
            run(build_alembic_config(f"{url}?timeout=0"))
    finally:
        other.close()


def test_a_run_holds_the_write_lock_before_its_first_write(tmp_path: Path) -> None:
    """A run keeps SQLite's write lock from its first statement, not from its first write.

    The injected revision is the only one the run applies, and nothing has
    been written when a second connection asks for the write lock from
    inside it, without waiting: the request is refused. A run begun with a
    plain BEGIN would hold only a read lock at that point, and the second
    connection would get the write lock.
    """
    url = _sqlite_url(tmp_path, "held.db")
    start = head_revision()
    assert start is not None
    config = _injected_config(
        _scripts_with_a_revision_after_head(tmp_path, _ASK_FOR_THE_WRITE_LOCK), url
    )
    command.upgrade(config, start)

    command.upgrade(config, "head")

    assert current_revision(url) == INJECTED_REVISION


def test_an_sqlite_connection_without_explicit_transactions_is_refused(tmp_path: Path) -> None:
    """A new connection left to the sqlite3 driver's own transaction handling is refused.

    That handling, the driver's default and what the application's engine
    keeps, opens a transaction only before a statement that changes data, so
    DDL issued earlier is committed at once and a failed run could leave it
    behind. The refusal comes before any revision runs.
    """
    url = _sqlite_url(tmp_path, "handed.db")
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            config = build_alembic_config(url)
            config.attributes["connection"] = connection
            with pytest.raises(CommandError, match="not inside a database transaction"):
                command.upgrade(config, "head")
    finally:
        engine.dispose()
    assert current_revision(url) is None
    assert _table_names(url) == []


def test_a_connection_in_autocommit_is_refused(database_url: str) -> None:
    """A connection in autocommit is refused on either database, before any revision runs.

    Autocommit commits each statement as it runs, whatever the migrations ask
    of the connection. On SQLite, SQLAlchemy's AUTOCOMMIT sets the sqlite3
    connection's isolation_level to None, the value the transaction recipe
    sets too, so what the refusal looks at is whether a transaction is
    actually open.
    """
    engine = build_engine(database_url)
    try:
        with engine.connect() as connection:
            config = build_alembic_config(database_url)
            config.attributes["connection"] = connection.execution_options(
                isolation_level="AUTOCOMMIT"
            )
            with pytest.raises(CommandError, match="not inside a database transaction"):
                command.upgrade(config, "head")
    finally:
        engine.dispose()
    assert current_revision(database_url) is None
    assert _table_names(database_url) == []


def test_a_connection_inside_a_transaction_is_used(database_url: str) -> None:
    """A handed connection whose transactions are real migrates, and the run commits."""
    engine = _transactional_engine(database_url)
    try:
        with engine.connect() as connection:
            config = build_alembic_config(database_url)
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()
    assert current_revision(database_url) == head_revision()


def test_an_sqlite_connection_with_autocommit_false_is_used(tmp_path: Path) -> None:
    """sqlite3's ``autocommit=False`` keeps a transaction open, so the connection is used.

    Python 3.12 added that mode for PEP 249 transaction handling. It leaves
    isolation_level at the driver's default, which is why the refusal does
    not go by that attribute.
    """
    url = _sqlite_url(tmp_path, "autocommit-false.db")
    engine = create_engine(url, connect_args={"autocommit": False})
    try:
        with engine.connect() as connection:
            assert connection.connection.driver_connection.isolation_level == ""
            config = build_alembic_config(url)
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()
    assert current_revision(url) == head_revision()


def test_an_sqlite_connection_with_autocommit_true_is_refused(tmp_path: Path) -> None:
    """sqlite3's ``autocommit=True`` is refused, even with a transaction open on it.

    In that mode the driver's commit() and rollback() do nothing, so a run
    over such a connection could not end its own transaction. Given an
    engine that emits BEGIN from its 'begin' event, the run used to start
    inside a transaction, report success, and leave nothing behind once the
    connection closed.
    """
    url = _sqlite_url(tmp_path, "autocommit-true.db")
    engine = create_engine(url, connect_args={"autocommit": True})

    @event.listens_for(engine, "begin")
    def _begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN")

    try:
        with engine.connect() as connection:
            config = build_alembic_config(url)
            config.attributes["connection"] = connection
            with pytest.raises(CommandError, match="autocommit=True"):
                command.upgrade(config, "head")
    finally:
        engine.dispose()
    assert current_revision(url) is None
    assert _table_names(url) == []


def test_a_connection_inside_its_callers_transaction_migrates_inside_it(
    database_url: str,
) -> None:
    """A handed connection on which the caller has begun a transaction is used, inside it.

    Alembic leaves a transaction it did not begin to whoever began it, so the
    run is kept only if the caller commits. Here the caller writes a row
    first, which on SQLite is what opens a transaction on a connection from
    build_engine, and rolls back after the run: the stamp it read inside its
    transaction is gone, and so is every table of the chain.
    """
    engine = build_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("CREATE TABLE caller_table (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO caller_table (id) VALUES (1)"))
            config = build_alembic_config(database_url)
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            stamp = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            connection.rollback()
    finally:
        engine.dispose()
    assert stamp == head_revision()
    assert current_revision(database_url) is None
    # On SQLite the caller's CREATE TABLE ran before its transaction began.
    assert set(_table_names(database_url)) <= {"caller_table"}


@pytest.mark.parametrize(
    "url",
    ["sqlite:///offline.db", "postgresql+psycopg://cfm@localhost/cfm"],
    ids=["sqlite", "postgresql"],
)
def test_an_offline_script_is_one_transaction(url: str) -> None:
    """``upgrade --sql`` prints one BEGIN before the first revision and one COMMIT after the last.

    PostgreSQL scripts always had them. SQLite scripts had none, so each
    statement of an applied script committed on its own. Offline mode
    renders SQL without connecting, so neither case needs a database. The
    script starts after 0002, whose data migration reads the users table and
    so cannot be rendered offline.
    """
    buffer = io.StringIO()
    config = Config(output_buffer=buffer)
    config.set_main_option("script_location", str(SCRIPTS_PATH))
    config.attributes["database_url"] = url
    command.upgrade(config, "0002:head", sql=True)
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    assert lines[0] == "BEGIN;"
    assert lines[-1] == "COMMIT;"
    assert lines.count("BEGIN;") == lines.count("COMMIT;") == 1


def test_the_application_engine_keeps_the_drivers_transaction_handling(tmp_path: Path) -> None:
    """Only the migration run takes transaction control from the sqlite3 driver.

    The migrations build their engine with the function the application uses,
    so whatever they attach must stay on their own engine: after an upgrade
    in the same process, a new application engine still leaves transactions
    to the driver and emits no BEGIN of its own.
    """
    url = _sqlite_url(tmp_path, "application.db")
    upgrade_to_head(url)
    engine = build_engine(url)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_connection: Connection, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT 1"))
            isolation_level = connection.connection.driver_connection.isolation_level
    finally:
        engine.dispose()
    assert isolation_level == ""
    assert statements == ["SELECT 1"]
