"""Append-only enforcement against inserts that replace a stored row.

Migration ``0004`` refuses ``UPDATE`` and ``DELETE`` on the audit and
evidence tables, and on SQLite that left an insert free to rewrite a row.
``INSERT OR REPLACE`` and ``REPLACE INTO`` resolve a collision with a
stored key by deleting the stored row and inserting the new one, and that
deletion fires no delete trigger unless the connection has turned
``recursive_triggers`` on, which nothing here does. One statement carrying
the id of an audit entry replaced the whole entry, actor and action
included, and neither trigger fired.

This migration gives each table a ``BEFORE INSERT`` trigger that aborts an
insert colliding with a stored row on any key the table has, before
SQLite can delete anything:

- ``audit_entries`` and ``commissioning_evidence``: the integer id, which
  is the rowid;
- ``evidence_objects``: the text id, the content hash, and the rowid that
  SQLite keeps beside a text primary key, which an insert may name;
- ``evidence_attachments``: the integer id, and each pair of object and
  record that the unique constraints allow once.

NULL equals nothing, so a column with no record in it collides with
nothing, as in those unique constraints. The trigger cannot tell a
collision that a conflict clause would have resolved from one the insert
would have failed on, and refuses both: ``INSERT OR IGNORE`` and
``ON CONFLICT DO NOTHING`` now fail instead of skipping the row, and a
plain colliding insert fails with the append-only message rather than the
key's constraint message. An upsert's update was already refused by the
update trigger; this trigger now answers first.

SQLite documents ``NEW.rowid`` as undefined in a ``BEFORE INSERT`` trigger
when the insert leaves the rowid to it, and the value it shows there is
-1. A stored row under rowid -1 would therefore collide with every insert
that leaves the id to SQLite, the application's included. SQLite usually
numbers a new row one above the highest rowid stored, and never below zero
unless an insert has named a negative rowid: a row named -2 in a table
with no higher rowid makes -1 the next. Each table therefore also gets an
``AFTER INSERT`` trigger, where ``NEW.rowid`` is the rowid the row was
given, that aborts an insert storing a row under a negative rowid. A row
stored that way before this revision would do the same harm, so the
upgrade refuses to start while a protected table holds one, and names each
such table with up to ten of its rowids. Exempting -1 from the collision
guard instead would let ``REPLACE`` rewrite a row stored under it. The
check reads the database, so a script rendered with ``--sql`` installs the
triggers without it.

PostgreSQL gets no DDL. It has no ``REPLACE``, and
``INSERT ... ON CONFLICT DO UPDATE`` and ``MERGE ... WHEN MATCHED THEN
UPDATE`` fire the statement-level update trigger from ``0004`` before they
reach a row; ``tests/test_append_only.py`` shows both refused on CI's
PostgreSQL job.

The downgrade drops the eight triggers; no data is involved. The tables and
their keys are frozen here on purpose, as in ``0004``.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.util import CommandError

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLLISIONS: dict[str, str] = {
    "audit_entries": "id = NEW.id",
    "commissioning_evidence": "id = NEW.id",
    "evidence_attachments": (
        "id = NEW.id"
        " OR (evidence_object_id = NEW.evidence_object_id"
        " AND (commissioning_test_id = NEW.commissioning_test_id"
        " OR punch_item_id = NEW.punch_item_id"
        " OR order_id = NEW.order_id"
        " OR permit_id = NEW.permit_id))"
    ),
    "evidence_objects": "rowid = NEW.rowid OR id = NEW.id OR sha256 = NEW.sha256",
}
"""Each protected table, and when an incoming row collides with a stored one.

Inside the trigger ``NEW`` is the incoming row, and the bare columns belong
to the stored row the subquery reads.
"""

ROWIDS_NAMED: int = 10
"""How many negative rowids the refusal names for one table before it counts the rest."""


def upgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        return
    if not context.as_sql:
        _refuse_negative_rowids()
    for table, collides in COLLISIONS.items():
        # Every name and condition comes from the constant above, never from input.
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_replace BEFORE INSERT ON {table} "  # noqa: S608
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {collides}) BEGIN "
            f"SELECT RAISE(ABORT, '{table} is append-only: "
            "an INSERT colliding with an existing row is not allowed'); "
            "END"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_negative_rowid AFTER INSERT ON {table} "
            "WHEN NEW.rowid < 0 BEGIN "
            f"SELECT RAISE(ABORT, '{table} is append-only: "
            "an INSERT under a negative rowid is not allowed'); "
            "END"
        )


def _refuse_negative_rowids() -> None:
    """Stop the upgrade, before any DDL, while a protected row has a negative rowid."""
    connection = op.get_bind()
    found: list[str] = []
    for table in COLLISIONS:
        # Table names come from the constant above, never from input.
        count: int = connection.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE rowid < 0")  # noqa: S608
        ).scalar_one()
        if not count:
            continue
        named: Sequence[int] = (
            connection.execute(
                sa.text(
                    f"SELECT rowid FROM {table} WHERE rowid < 0"  # noqa: S608
                    f" ORDER BY rowid DESC LIMIT {ROWIDS_NAMED}"
                )
            )
            .scalars()
            .all()
        )
        listed = ", ".join(str(rowid) for rowid in named)
        if count > len(named):
            listed += f" and {count - len(named)} more"
        found.append(f"{table} ({listed})")
    if found:
        raise CommandError(
            "Revision 0005 cannot be applied while a protected table holds a row under a "
            f"negative rowid: {'; '.join(found)}. Such rows exist only in a table where an "
            "insert has named a negative rowid, which the application never does. The guards "
            "this revision adds refuse such inserts, and rows like these can make them refuse "
            "the inserts that leave the rowid to the database as well. Remove the rows, which "
            "is a deletion like any other (see 'Retention and legal hold' in the admin guide), "
            "and run the upgrade again."
        )


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        return
    for table in COLLISIONS:
        op.execute(f"DROP TRIGGER trg_{table}_no_negative_rowid")
        op.execute(f"DROP TRIGGER trg_{table}_no_replace")
