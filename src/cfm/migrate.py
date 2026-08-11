"""Programmatic access to the Alembic migration chain.

The application uses these helpers to verify or advance the database schema
at startup, and the bootstrap and demo seed scripts use them to bring a
fresh database to the head revision. The Alembic configuration is built in
memory and points at the migration scripts packaged inside :mod:`cfm`, so
the helpers behave the same from a checkout and from an installed package.
The database URL travels through ``config.attributes`` instead of the ini
file, which keeps :class:`cfm.config.Settings` the single source of truth
and avoids configparser interpolation of URLs.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from .database import build_engine

SCRIPTS_PATH = Path(__file__).resolve().parent / "migrations"

_STAMP_HINT = (
    "If this database was created by a release that predates migrations "
    "(tables built at startup), verify that its schema matches the current "
    "models, adopt it with 'alembic stamp 0001', and then run "
    "'alembic upgrade head'."
)


class SchemaOutOfDateError(RuntimeError):
    """Raised when the database schema is not at the head revision."""


def build_alembic_config(database_url: str) -> Config:
    """An in-memory Alembic configuration bound to ``database_url``."""
    config = Config()
    config.set_main_option("script_location", str(SCRIPTS_PATH))
    config.attributes["database_url"] = database_url
    return config


def head_revision() -> str | None:
    """The newest revision in the packaged migration scripts."""
    return ScriptDirectory(str(SCRIPTS_PATH)).get_current_head()


def current_revision(database_url: str) -> str | None:
    """The revision stamped on the database, or ``None`` when unstamped."""
    engine = build_engine(database_url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def _has_tables(database_url: str) -> bool:
    engine = build_engine(database_url)
    try:
        return bool(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def upgrade_to_head(database_url: str) -> None:
    """Run every pending migration against ``database_url``."""
    command.upgrade(build_alembic_config(database_url), "head")


def ensure_schema_current(database_url: str) -> None:
    """Verify that the database is at the head revision.

    Raises :class:`SchemaOutOfDateError` with instructions for the operator
    when the database is empty, predates migrations, or is behind head.
    """
    head = head_revision()
    current = current_revision(database_url)
    if current == head:
        return
    if current is None:
        if _has_tables(database_url):
            raise SchemaOutOfDateError(
                f"The configured database has tables but no Alembic revision stamp. {_STAMP_HINT}"
            )
        raise SchemaOutOfDateError(
            "The configured database is empty. Run 'alembic upgrade head' "
            "before starting the application, or set CFM_DB_AUTO_UPGRADE=true "
            "to let the application migrate at startup."
        )
    raise SchemaOutOfDateError(
        f"The database schema is at revision {current} but the application "
        f"expects {head}. Run 'alembic upgrade head' before starting the "
        "application, or set CFM_DB_AUTO_UPGRADE=true to let the application "
        "migrate at startup."
    )


def ensure_fresh_schema(database_url: str) -> None:
    """Bring an empty or already-migrated database to the head revision.

    Used by the bootstrap and demo seed scripts, which only ever target
    fresh databases. A database that has tables but no revision stamp is
    left untouched and reported, because migrating over it would fail
    halfway through.
    """
    current = current_revision(database_url)
    if current is None and _has_tables(database_url):
        raise SchemaOutOfDateError(
            f"The configured database has tables but no Alembic revision stamp. {_STAMP_HINT}"
        )
    upgrade_to_head(database_url)
