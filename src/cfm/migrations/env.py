"""Alembic environment wired to the application's settings and metadata.

The database connection is resolved in this order:

1. ``config.attributes["connection"]`` — an existing SQLAlchemy connection,
   for callers that already hold one.
2. ``config.attributes["database_url"]`` — a URL override, used by
   :mod:`cfm.migrate` so URLs never pass through configparser interpolation.
3. :func:`cfm.config.get_settings` — the same settings the API uses
   (``CFM_DATABASE_URL``, an optional ``.env`` file, or the SQLite default).

Every run is one transaction, on SQLite as on PostgreSQL: when anything in it
fails, the schema and the revision stamp stay where the run started.
PostgreSQL gives that by itself. On SQLite, Alembic by default treats DDL as
non-transactional and commits after each revision, and the ``sqlite3``
driver's own transaction handling opens a transaction only before a statement
that changes data, so a ``CREATE TABLE`` or ``ADD COLUMN`` issued before one
would be permanent at once. The run is therefore configured as transactional
DDL, and the SQLite engine the migrations open for themselves takes
transaction control away from the driver and starts each transaction with
``BEGIN IMMEDIATE``, which also takes the database's write lock before
anything is read. The application's own engine is built separately and keeps
the driver's default.

A connection handed in through ``config.attributes`` belongs to its caller,
so it is checked rather than changed. Every run is checked before its first
revision, on either kind of connection: it starts only if its statements will
land in one transaction. On SQLite that means a transaction must already be
open when the run begins, which is not the case for a new connection left to
the driver's own handling, and that the driver can end it, which it cannot in
``sqlite3``'s ``autocommit=True`` mode; on any database it rules out
autocommit.

SQLite can also end the transaction by itself part-way through a run. After
some errors, a conflict resolved with ``ROLLBACK`` or ``RAISE(ROLLBACK)`` in a
trigger among them, it rolls back the whole transaction rather than the
statement, and a revision that caught such an error and carried on would have
every later statement committed on its own, because the driver no longer
opens transactions. While a run is in progress, its SQLite connection
therefore refuses any statement issued after the run's transaction has ended,
as PostgreSQL refuses every statement after an error until the transaction
ends.

Offline mode (``--sql``) wraps the script it prints in one ``BEGIN`` and one
``COMMIT`` on both databases. On SQLite, applying it is one transaction only
if whatever applies it stops at the first error without committing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from logging.config import fileConfig
from typing import Any

from alembic import context
from alembic.util import CommandError
from sqlalchemy import Connection, Engine, event

from cfm import models  # noqa: F401  # registers every table on Base.metadata
from cfm.config import get_settings
from cfm.database import Base, build_engine

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

target_metadata = Base.metadata

_NOT_ONE_TRANSACTION = (
    "Refusing to migrate over a connection that is not inside a database transaction: "
    "each statement would be committed as it ran, and a run that failed part-way would "
    "leave the schema half-upgraded. Hand over a connection whose transactions are real "
    "(on SQLite, set isolation_level = None on the sqlite3 connection and emit BEGIN from "
    "the engine's 'begin' event, or connect with autocommit=False; on any database, do "
    "not use AUTOCOMMIT), or pass config.attributes['database_url'] instead and let the "
    "migrations open a connection of their own."
)

_COMMIT_DOES_NOTHING = (
    "Refusing to migrate over an sqlite3 connection opened with autocommit=True: in that "
    "mode the driver's commit() and rollback() do nothing, so the run could not end its own "
    "transaction. Connect with autocommit=False, or with the default (and then set "
    "isolation_level = None on the sqlite3 connection and emit BEGIN from the engine's "
    "'begin' event), or pass config.attributes['database_url'] instead and let the "
    "migrations open a connection of their own."
)

_TRANSACTION_ENDED = (
    "Refusing to go on: the migration's transaction ended before the run did, so this "
    "statement would be committed on its own. SQLite rolls back the whole transaction "
    "after some errors, a conflict resolved with ROLLBACK or RAISE(ROLLBACK) in a trigger "
    "among them; a revision has to let such an error end the run."
)


def _database_url() -> str:
    override = context.config.attributes.get("database_url")
    if override is not None:
        return str(override)
    return get_settings().database_url


def _leave_transactions_to_sqlalchemy(dbapi_connection: Any, _connection_record: Any) -> None:
    # The driver issues no BEGIN of its own, so it no longer decides which
    # statements run inside a transaction.
    dbapi_connection.isolation_level = None


def _begin_immediately(connection: Connection) -> None:
    connection.exec_driver_sql("BEGIN IMMEDIATE")


def _migration_engine(url: str) -> Engine:
    """The application's engine, made to run each transaction for real on SQLite.

    The two listeners are the recipe in SQLAlchemy's SQLite dialect
    documentation, with ``BEGIN IMMEDIATE`` in place of ``BEGIN``. They are
    attached to this engine only.
    """
    engine = build_engine(url)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _leave_transactions_to_sqlalchemy)
        event.listen(engine, "begin", _begin_immediately)
    return engine


def _require_one_transaction(connection: Connection) -> None:
    """Refuse to run unless every statement of the run lands in one transaction it can end."""
    driver_connection = connection.connection.driver_connection
    if connection.dialect.name == "sqlite":
        if getattr(driver_connection, "autocommit", None) is True:
            # A transaction begun with an explicit BEGIN would stay open
            # whatever the run asked of the driver.
            raise CommandError(_COMMIT_DOES_NOTHING)
        # sqlite3 reports whether SQLite itself has a transaction open.
        transactional = bool(getattr(driver_connection, "in_transaction", False))
    else:
        # The driver opens a transaction before the first statement unless
        # the connection is in autocommit.
        transactional = not getattr(driver_connection, "autocommit", False)
    if not transactional:
        raise CommandError(_NOT_ONE_TRANSACTION)


def _refuse_outside_the_transaction(
    connection: Connection, _cursor: Any, _statement: str, *_rest: Any
) -> None:
    # SQLAlchemy still counts the run's transaction as open, but SQLite no
    # longer has one. The BEGIN that _begin_immediately emits when a new
    # transaction starts, as one does after Alembic's autocommit_block(), runs
    # before SQLAlchemy counts that transaction, and so passes.
    if connection.in_transaction() and not getattr(
        connection.connection.driver_connection, "in_transaction", False
    ):
        raise CommandError(_TRANSACTION_ENDED)


@contextmanager
def _held_to_its_transaction(connection: Connection) -> Iterator[None]:
    """On SQLite, refuse every statement the run issues once its transaction has ended."""
    if connection.dialect.name != "sqlite":
        # PostgreSQL refuses every statement after an error until the
        # transaction ends.
        yield
        return
    event.listen(connection, "before_cursor_execute", _refuse_outside_the_transaction)
    try:
        yield
    finally:
        event.remove(connection, "before_cursor_execute", _refuse_outside_the_transaction)


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=connection.dialect.name == "sqlite",
        # One transaction for the whole run, which PostgreSQL has by default;
        # SQLite would otherwise get one per revision.
        transactional_ddl=True,
    )


def run_migrations_offline() -> None:
    """Emit migration SQL without connecting to a database."""
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_in_one_transaction(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        _require_one_transaction(connection)
        with _held_to_its_transaction(connection):
            context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection, as one transaction."""
    connection: Connection | None = context.config.attributes.get("connection")
    if connection is not None:
        _run_in_one_transaction(connection)
        return
    engine = _migration_engine(_database_url())
    try:
        with engine.connect() as owned_connection:
            _run_in_one_transaction(owned_connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
