"""Database-level append-only enforcement for audit and evidence tables.

``audit_entries``, ``commissioning_evidence``, ``evidence_attachments``,
and ``evidence_objects`` were append-only by convention only: the
application exposes no update or delete path, but an application bug, a
migration mistake, or anyone holding database credentials could rewrite
history silently. This migration binds the invariant to the schema itself
with triggers that abort any UPDATE or DELETE (and TRUNCATE on
PostgreSQL, which SQLite does not have), so it holds for every role —
including the application's own. Triggers beat REVOKE-based grants here
because grants only bind connection roles, and the application connects
as one role for all its work; a trigger fires whoever is connected.
Honest limit: a PostgreSQL superuser (or anyone who can run DDL, such as
a later migration) can drop the trigger — the protection is against
silent mutation, not against deliberate schema changes, which stay
visible in migration history.

On SQLite one trigger per operation raises ABORT with a fixed message;
a statement matching no rows never fires them, which is fine — no row
changes either way. On PostgreSQL (11 or newer) a single ``plpgsql``
function raises for whichever operation fired it, attached per table as
one statement-level trigger, so even a zero-row UPDATE or DELETE
statement errors. CI's PostgreSQL job runs this branch for real: the
migration chain executes it in both directions, and the append-only
tests run against that server, so the triggers are asserted to exist
and to fire on the dialect the application deploys against.

Alembic autogenerate cannot see triggers, so the schema drift checks
stay green without knowing about them; ``tests/test_append_only.py``
asserts existence and firing instead. Any later migration that rebuilds
one of these tables (SQLite batch mode drops and recreates the table,
discarding its triggers) must recreate the triggers or that test fails.
The tables list is frozen here on purpose — migrations must not follow
the application code they predate.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES: tuple[str, ...] = (
    "audit_entries",
    "commissioning_evidence",
    "evidence_attachments",
    "evidence_objects",
)

POSTGRESQL_FUNCTION = """
CREATE FUNCTION cfm_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
END;
$$
"""


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(POSTGRESQL_FUNCTION)
        for table in APPEND_ONLY_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_append_only "
                f"BEFORE UPDATE OR DELETE OR TRUNCATE ON {table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION cfm_append_only()"
            )
        return
    for table in APPEND_ONLY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} BEGIN "
                f"SELECT RAISE(ABORT, '{table} is append-only: {operation} is not allowed'); "
                "END"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in APPEND_ONLY_TABLES:
            op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
        op.execute("DROP FUNCTION cfm_append_only()")
        return
    for table in APPEND_ONLY_TABLES:
        for operation in ("update", "delete"):
            op.execute(f"DROP TRIGGER trg_{table}_no_{operation}")
