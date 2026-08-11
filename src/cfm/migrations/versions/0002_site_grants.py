"""Site-scoped write authority.

Adds the ``site_grants`` table — where a user's write authority applies:
one row per (user, site), or ``site_id`` null for an installation-wide
grant — and a nullable ``scope`` column on ``audit_entries`` recording the
authorization scope each domain write was covered by.

The data migration gives every existing user one installation-wide grant,
so every token keeps exactly the authority it had before this revision.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("site_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "site_id", name="uq_site_grants_user_site"),
    )
    with op.batch_alter_table("site_grants", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_site_grants_site_id"), ["site_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_site_grants_user_id"), ["user_id"], unique=False)

    with op.batch_alter_table("audit_entries", schema=None) as batch_op:
        batch_op.add_column(sa.Column("scope", sa.String(length=64), nullable=True))

    # Every user that predates site scoping keeps installation-wide authority.
    connection = op.get_bind()
    user_ids = connection.execute(sa.text("SELECT id FROM users")).scalars().all()
    if user_ids:
        site_grants = sa.table(
            "site_grants",
            sa.column("id", sa.String(length=36)),
            sa.column("user_id", sa.String(length=36)),
            sa.column("site_id", sa.String(length=36)),
            sa.column("created_at", sa.DateTime(timezone=True)),
        )
        now = datetime.now(UTC)
        op.bulk_insert(
            site_grants,
            [
                {"id": str(uuid4()), "user_id": user_id, "site_id": None, "created_at": now}
                for user_id in user_ids
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_entries", schema=None) as batch_op:
        batch_op.drop_column("scope")

    with op.batch_alter_table("site_grants", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_site_grants_user_id"))
        batch_op.drop_index(batch_op.f("ix_site_grants_site_id"))

    op.drop_table("site_grants")
