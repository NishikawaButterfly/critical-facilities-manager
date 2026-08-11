"""Schema baseline.

Captures exactly the schema the models declared when migrations were
introduced: a fresh database migrated to this revision matches what
``Base.metadata.create_all`` used to build. Databases that already carry
that schema adopt the chain with ``alembic stamp 0001``.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_entries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_entries_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_entries_actor"), ["actor"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_entries_entity_id"), ["entity_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_audit_entries_entity_type"), ["entity_type"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_audit_entries_occurred_at"), ["occurred_at"], unique=False
        )

    op.create_table(
        "constraints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("constraints", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_constraints_kind"), ["kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_constraints_status"), ["status"], unique=False)

    op.create_table(
        "locations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parent_id", "code", name="uq_locations_parent_code"),
    )
    with op.batch_alter_table("locations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_locations_code"), ["code"], unique=False)
        batch_op.create_index(batch_op.f("ix_locations_kind"), ["kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_locations_parent_id"), ["parent_id"], unique=False)

    op.create_table(
        "procedures",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("predecessor_id", sa.String(length=36), nullable=True),
        sa.Column("last_edited_by", sa.String(length=128), nullable=False),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["predecessor_id"], ["procedures.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("predecessor_id", name="uq_procedures_predecessor"),
    )
    with op.batch_alter_table("procedures", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_procedures_kind"), ["kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_procedures_status"), ["status"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_is_active"), ["is_active"], unique=False)
        batch_op.create_index(batch_op.f("ix_users_role"), ["role"], unique=False)
        batch_op.create_index(batch_op.f("ix_users_username"), ["username"], unique=True)

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_api_tokens_revoked"), ["revoked"], unique=False)
        batch_op.create_index(batch_op.f("ix_api_tokens_token_hash"), ["token_hash"], unique=True)
        batch_op.create_index(batch_op.f("ix_api_tokens_user_id"), ["user_id"], unique=False)

    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("asset_type", sa.String(length=128), nullable=False),
        sa.Column("criticality", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("location_id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["site_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("site_id", "tag", name="uq_assets_site_tag"),
    )
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_assets_asset_type"), ["asset_type"], unique=False)
        batch_op.create_index(batch_op.f("ix_assets_criticality"), ["criticality"], unique=False)
        batch_op.create_index(batch_op.f("ix_assets_location_id"), ["location_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_assets_site_id"), ["site_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_assets_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_assets_tag"), ["tag"], unique=False)

    op.create_table(
        "constraint_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("constraint_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["constraint_id"], ["constraints.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("constraint_id", "asset_id", name="uq_constraint_members_pair"),
    )
    with op.batch_alter_table("constraint_members", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_constraint_members_asset_id"), ["asset_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_constraint_members_constraint_id"), ["constraint_id"], unique=False
        )

    op.create_table(
        "maintenance_orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("completion_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("maintenance_orders", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_maintenance_orders_asset_id"), ["asset_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_maintenance_orders_due_date"), ["due_date"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_maintenance_orders_order_type"), ["order_type"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_maintenance_orders_status"), ["status"], unique=False)

    op.create_table(
        "punch_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=True),
        sa.Column("location_id", sa.String(length=36), nullable=True),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("started_by", sa.String(length=128), nullable=True),
        sa.Column("verified_by", sa.String(length=128), nullable=True),
        sa.Column("closing_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("punch_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_punch_items_asset_id"), ["asset_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_punch_items_category"), ["category"], unique=False)
        batch_op.create_index(batch_op.f("ix_punch_items_due_date"), ["due_date"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_punch_items_location_id"), ["location_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_punch_items_severity"), ["severity"], unique=False)
        batch_op.create_index(batch_op.f("ix_punch_items_status"), ["status"], unique=False)

    op.create_table(
        "commissioning_tests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("procedure_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("executed_by", sa.String(length=128), nullable=True),
        sa.Column("witnessed_by", sa.String(length=128), nullable=True),
        sa.Column("punch_item_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["procedure_id"], ["procedures.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["punch_item_id"], ["punch_items.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("commissioning_tests", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_commissioning_tests_asset_id"), ["asset_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_commissioning_tests_planned_date"), ["planned_date"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_commissioning_tests_procedure_id"), ["procedure_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_commissioning_tests_punch_item_id"), ["punch_item_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_commissioning_tests_status"), ["status"], unique=False)

    op.create_table(
        "incidents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("corrective_order_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["corrective_order_id"], ["maintenance_orders.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("incidents", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_incidents_asset_id"), ["asset_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_incidents_corrective_order_id"), ["corrective_order_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_incidents_severity"), ["severity"], unique=False)
        batch_op.create_index(batch_op.f("ix_incidents_status"), ["status"], unique=False)

    op.create_table(
        "work_permits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("procedure_id", sa.String(length=36), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("issued_by", sa.String(length=128), nullable=True),
        sa.Column("completion_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["maintenance_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["procedure_id"], ["procedures.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("work_permits", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_work_permits_order_id"), ["order_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_work_permits_procedure_id"), ["procedure_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_work_permits_status"), ["status"], unique=False)

    op.create_table(
        "commissioning_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("test_id", sa.String(length=36), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["test_id"], ["commissioning_tests.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("commissioning_evidence", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_commissioning_evidence_test_id"), ["test_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("commissioning_evidence", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_commissioning_evidence_test_id"))

    op.drop_table("commissioning_evidence")
    with op.batch_alter_table("work_permits", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_work_permits_status"))
        batch_op.drop_index(batch_op.f("ix_work_permits_procedure_id"))
        batch_op.drop_index(batch_op.f("ix_work_permits_order_id"))

    op.drop_table("work_permits")
    with op.batch_alter_table("incidents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_incidents_status"))
        batch_op.drop_index(batch_op.f("ix_incidents_severity"))
        batch_op.drop_index(batch_op.f("ix_incidents_corrective_order_id"))
        batch_op.drop_index(batch_op.f("ix_incidents_asset_id"))

    op.drop_table("incidents")
    with op.batch_alter_table("commissioning_tests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_commissioning_tests_status"))
        batch_op.drop_index(batch_op.f("ix_commissioning_tests_punch_item_id"))
        batch_op.drop_index(batch_op.f("ix_commissioning_tests_procedure_id"))
        batch_op.drop_index(batch_op.f("ix_commissioning_tests_planned_date"))
        batch_op.drop_index(batch_op.f("ix_commissioning_tests_asset_id"))

    op.drop_table("commissioning_tests")
    with op.batch_alter_table("punch_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_punch_items_status"))
        batch_op.drop_index(batch_op.f("ix_punch_items_severity"))
        batch_op.drop_index(batch_op.f("ix_punch_items_location_id"))
        batch_op.drop_index(batch_op.f("ix_punch_items_due_date"))
        batch_op.drop_index(batch_op.f("ix_punch_items_category"))
        batch_op.drop_index(batch_op.f("ix_punch_items_asset_id"))

    op.drop_table("punch_items")
    with op.batch_alter_table("maintenance_orders", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_maintenance_orders_status"))
        batch_op.drop_index(batch_op.f("ix_maintenance_orders_order_type"))
        batch_op.drop_index(batch_op.f("ix_maintenance_orders_due_date"))
        batch_op.drop_index(batch_op.f("ix_maintenance_orders_asset_id"))

    op.drop_table("maintenance_orders")
    with op.batch_alter_table("constraint_members", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_constraint_members_constraint_id"))
        batch_op.drop_index(batch_op.f("ix_constraint_members_asset_id"))

    op.drop_table("constraint_members")
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_assets_tag"))
        batch_op.drop_index(batch_op.f("ix_assets_status"))
        batch_op.drop_index(batch_op.f("ix_assets_site_id"))
        batch_op.drop_index(batch_op.f("ix_assets_location_id"))
        batch_op.drop_index(batch_op.f("ix_assets_criticality"))
        batch_op.drop_index(batch_op.f("ix_assets_asset_type"))

    op.drop_table("assets")
    with op.batch_alter_table("api_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_tokens_user_id"))
        batch_op.drop_index(batch_op.f("ix_api_tokens_token_hash"))
        batch_op.drop_index(batch_op.f("ix_api_tokens_revoked"))

    op.drop_table("api_tokens")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_username"))
        batch_op.drop_index(batch_op.f("ix_users_role"))
        batch_op.drop_index(batch_op.f("ix_users_is_active"))

    op.drop_table("users")
    with op.batch_alter_table("procedures", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_procedures_status"))
        batch_op.drop_index(batch_op.f("ix_procedures_kind"))

    op.drop_table("procedures")
    with op.batch_alter_table("locations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_locations_parent_id"))
        batch_op.drop_index(batch_op.f("ix_locations_kind"))
        batch_op.drop_index(batch_op.f("ix_locations_code"))

    op.drop_table("locations")
    with op.batch_alter_table("constraints", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_constraints_status"))
        batch_op.drop_index(batch_op.f("ix_constraints_kind"))

    op.drop_table("constraints")
    with op.batch_alter_table("audit_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_entries_occurred_at"))
        batch_op.drop_index(batch_op.f("ix_audit_entries_entity_type"))
        batch_op.drop_index(batch_op.f("ix_audit_entries_entity_id"))
        batch_op.drop_index(batch_op.f("ix_audit_entries_actor"))
        batch_op.drop_index(batch_op.f("ix_audit_entries_action"))

    op.drop_table("audit_entries")
