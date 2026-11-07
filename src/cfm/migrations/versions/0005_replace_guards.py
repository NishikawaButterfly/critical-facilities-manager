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

SQLite shows a ``BEFORE INSERT`` trigger the value -1 for a rowid it has
yet to assign. A stored row whose rowid is -1, which only an explicit
insert can create and which nothing in the application writes, would
therefore collide with every insert that leaves the id to SQLite. The
guard protects that row like any other instead of exempting -1, which
would let ``REPLACE`` rewrite it.

PostgreSQL gets no DDL. It has no ``REPLACE``, and
``INSERT ... ON CONFLICT DO UPDATE`` and ``MERGE ... WHEN MATCHED THEN
UPDATE`` fire the statement-level update trigger from ``0004`` before they
reach a row; ``tests/test_append_only.py`` shows both refused on CI's
PostgreSQL job.

The downgrade drops the four triggers; no data is involved. The tables and
their keys are frozen here on purpose, as in ``0004``.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

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


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        return
    for table, collides in COLLISIONS.items():
        # Every name and condition comes from the constant above, never from input.
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_replace BEFORE INSERT ON {table} "  # noqa: S608
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {collides}) BEGIN "
            f"SELECT RAISE(ABORT, '{table} is append-only: "
            "an INSERT colliding with an existing row is not allowed'); "
            "END"
        )


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        return
    for table in COLLISIONS:
        op.execute(f"DROP TRIGGER trg_{table}_no_replace")
