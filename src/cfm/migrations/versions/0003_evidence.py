"""Verifiable file evidence.

Adds ``evidence_objects`` — one row per stored file, addressed by the
SHA-256 of its content (unique), with the size counted server-side and the
declaration made when the content first entered the store — and
``evidence_attachments``, one row per attach of an object to a
commissioning test, punch item, maintenance order, or work permit.
Exactly one target column is set per attachment (the service enforces it),
and the per-target unique constraints let one object attach to many
targets but only once to any single target. Both tables are append-only;
there is no data migration because no file evidence existed before this
revision.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_objects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("uploaded_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("evidence_objects", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_evidence_objects_sha256"), ["sha256"], unique=True)

    op.create_table(
        "evidence_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("evidence_object_id", sa.String(length=36), nullable=False),
        sa.Column("commissioning_test_id", sa.String(length=36), nullable=True),
        sa.Column("punch_item_id", sa.String(length=36), nullable=True),
        sa.Column("order_id", sa.String(length=36), nullable=True),
        sa.Column("permit_id", sa.String(length=36), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("attached_by", sa.String(length=128), nullable=False),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["commissioning_test_id"], ["commissioning_tests.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_object_id"], ["evidence_objects.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["order_id"], ["maintenance_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["permit_id"], ["work_permits.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["punch_item_id"], ["punch_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evidence_object_id", "commissioning_test_id", name="uq_evidence_attachments_test"
        ),
        sa.UniqueConstraint("evidence_object_id", "order_id", name="uq_evidence_attachments_order"),
        sa.UniqueConstraint(
            "evidence_object_id", "permit_id", name="uq_evidence_attachments_permit"
        ),
        sa.UniqueConstraint(
            "evidence_object_id", "punch_item_id", name="uq_evidence_attachments_punch_item"
        ),
    )
    with op.batch_alter_table("evidence_attachments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_evidence_attachments_commissioning_test_id"),
            ["commissioning_test_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_evidence_attachments_evidence_object_id"),
            ["evidence_object_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_evidence_attachments_order_id"), ["order_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_evidence_attachments_permit_id"), ["permit_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_evidence_attachments_punch_item_id"), ["punch_item_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("evidence_attachments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_evidence_attachments_punch_item_id"))
        batch_op.drop_index(batch_op.f("ix_evidence_attachments_permit_id"))
        batch_op.drop_index(batch_op.f("ix_evidence_attachments_order_id"))
        batch_op.drop_index(batch_op.f("ix_evidence_attachments_evidence_object_id"))
        batch_op.drop_index(batch_op.f("ix_evidence_attachments_commissioning_test_id"))

    op.drop_table("evidence_attachments")

    with op.batch_alter_table("evidence_objects", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_evidence_objects_sha256"))

    op.drop_table("evidence_objects")
