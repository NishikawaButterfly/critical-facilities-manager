"""Alembic environment wired to the application's settings and metadata.

The database connection is resolved in this order:

1. ``config.attributes["connection"]`` — an existing SQLAlchemy connection,
   for callers that already hold one.
2. ``config.attributes["database_url"]`` — a URL override, used by
   :mod:`cfm.migrate` so URLs never pass through configparser interpolation.
3. :func:`cfm.config.get_settings` — the same settings the API uses
   (``CFM_DATABASE_URL``, an optional ``.env`` file, or the SQLite default).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from cfm import models  # noqa: F401  # registers every table on Base.metadata
from cfm.config import get_settings
from cfm.database import Base, build_engine

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    override = context.config.attributes.get("database_url")
    if override is not None:
        return str(override)
    return get_settings().database_url


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=connection.dialect.name == "sqlite",
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
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connection: Connection | None = context.config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = build_engine(_database_url())
    try:
        with engine.connect() as owned_connection:
            _configure(owned_connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
