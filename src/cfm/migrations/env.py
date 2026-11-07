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
open when the run begins, which is not the case for a connection left to the
driver's own handling; on any database it rules out autocommit.

Offline mode (``--sql``) wraps the script it prints in one ``BEGIN`` and one
``COMMIT`` on both databases.
"""

from __future__ import annotations

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
    """Refuse to run unless every statement of the run lands in one transaction."""
    driver_connection = connection.connection.driver_connection
    if connection.dialect.name == "sqlite":
        # sqlite3 reports whether SQLite itself has a transaction open.
        transactional = bool(getattr(driver_connection, "in_transaction", False))
    else:
        # The driver opens a transaction before the first statement unless
        # the connection is in autocommit.
        transactional = not getattr(driver_connection, "autocommit", False)
    if not transactional:
        raise CommandError(_NOT_ONE_TRANSACTION)


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


def run_migrations_online() -> None:
    """Run migrations against a live connection, as one transaction."""
    connection: Connection | None = context.config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            _require_one_transaction(connection)
            context.run_migrations()
        return
    engine = _migration_engine(_database_url())
    try:
        with engine.connect() as owned_connection:
            _configure(owned_connection)
            with context.begin_transaction():
                _require_one_transaction(owned_connection)
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
