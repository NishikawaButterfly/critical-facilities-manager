"""SQLAlchemy ORM models for the domain core."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_public_id() -> str:
    return str(uuid4())


class Location(Base):
    """A node in the site → building → floor → room hierarchy."""

    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("parent_id", "code", name="uq_locations_parent_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    parent: Mapped[Location | None] = relationship(remote_side="Location.id")


class Asset(Base):
    """A piece of equipment tracked at a location.

    ``site_id`` is derived from the location hierarchy whenever the asset is
    created or moved, so the database can enforce tag uniqueness per site.
    """

    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("site_id", "tag", name="uq_assets_site_tag"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    tag: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    asset_type: Mapped[str] = mapped_column(String(128), index=True)
    criticality: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    location_id: Mapped[str] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    site_id: Mapped[str] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MaintenanceOrder(Base):
    """Preventive or corrective work against a single asset."""

    __tablename__ = "maintenance_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    order_type: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Procedure(Base):
    """A versioned operating procedure (MOP, SOP, or EOP).

    Versions form a chain through ``predecessor_id``: an approved procedure is
    frozen, and a new version starts as a fresh draft linked to it. The unique
    constraint keeps the chain linear (one successor per procedure).
    """

    __tablename__ = "procedures"
    __table_args__ = (UniqueConstraint("predecessor_id", name="uq_procedures_predecessor"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    kind: Mapped[str] = mapped_column(String(8), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(255))
    steps: Mapped[list[str]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)
    predecessor_id: Mapped[str | None] = mapped_column(
        ForeignKey("procedures.id", ondelete="RESTRICT"), nullable=True
    )
    last_edited_by: Mapped[str] = mapped_column(String(128))
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class WorkPermit(Base):
    """Authorization to carry out a maintenance order, optionally under a procedure."""

    __tablename__ = "work_permits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("maintenance_orders.id", ondelete="RESTRICT"), index=True
    )
    procedure_id: Mapped[str | None] = mapped_column(
        ForeignKey("procedures.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), index=True)
    requested_by: Mapped[str] = mapped_column(String(128))
    issued_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    completion_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Incident(Base):
    """An unplanned event observed on an asset.

    ``corrective_order_id`` links the corrective maintenance order created
    from the incident, when there is one.
    """

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrective_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("maintenance_orders.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PunchItem(Base):
    """A defect or gap found during commissioning or operations.

    A punch item is scoped to exactly one of an asset or a location; the
    service layer enforces that exactly one of the two references is set.
    ``started_by`` records who moved the item to in_progress so that closing
    can require a different verifier.
    """

    __tablename__ = "punch_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    location_id: Mapped[str | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    started_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    closing_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CommissioningTest(Base):
    """A witnessed acceptance test run on an asset before it enters service.

    Recording a result stores the executor (the request actor) and a witness
    that must be a different actor. ``punch_item_id`` links the punch item
    opened from a failed test, when there is one. Evidence entries are
    structured text notes only; file and photo evidence is future work
    pending object storage.
    """

    __tablename__ = "commissioning_tests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)
    procedure_id: Mapped[str | None] = mapped_column(
        ForeignKey("procedures.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    planned_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    executed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    witnessed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    punch_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("punch_items.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    evidence: Mapped[list[CommissioningEvidence]] = relationship(
        order_by="CommissioningEvidence.id"
    )


class CommissioningEvidence(Base):
    """One structured text note recorded as evidence on a commissioning test.

    Rows are only ever appended while the test is pending or as part of
    recording its result; nothing updates or deletes them.
    """

    __tablename__ = "commissioning_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_id: Mapped[str] = mapped_column(
        ForeignKey("commissioning_tests.id", ondelete="RESTRICT"), index=True
    )
    note: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(128))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Constraint(Base):
    """An operational constraint over a set of member assets.

    Constraints are created and retired, never edited. The
    ``mutual_exclusive_maintenance`` kind is enforced when maintenance
    orders start; the ``advisory`` kind carries free text in
    ``description`` and is only surfaced.
    """

    __tablename__ = "constraints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    members: Mapped[list[ConstraintMember]] = relationship(order_by="ConstraintMember.id")


class ConstraintMember(Base):
    """Membership of one asset in one constraint."""

    __tablename__ = "constraint_members"
    __table_args__ = (
        UniqueConstraint("constraint_id", "asset_id", name="uq_constraint_members_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    constraint_id: Mapped[str] = mapped_column(
        ForeignKey("constraints.id", ondelete="RESTRICT"), index=True
    )
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)


class User(Base):
    """A person or integration that can authenticate against the API.

    There are no passwords and no self-registration: authentication happens
    exclusively through API tokens (:class:`ApiToken`) minted by an admin.
    Users are deactivated, never deleted, so the usernames recorded in the
    audit trail keep resolving.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ApiToken(Base):
    """A bearer token that authenticates one user.

    Only the SHA-256 hash of the secret is stored; the plaintext is returned
    exactly once at creation time and cannot be recovered afterwards. Tokens
    are revoked, never deleted, and may carry an optional expiry.
    """

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    label: Mapped[str] = mapped_column(String(128))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SiteGrant(Base):
    """Where one user's write authority applies.

    The user's role says *what* they may do; their grants say *where*. A row
    with ``site_id`` set covers that site's whole subtree; a row with
    ``site_id`` null is an installation-wide grant covering every site,
    current and future. Objects that belong to no single site require an
    installation-wide grant to write. Reads are not scoped.
    """

    __tablename__ = "site_grants"
    __table_args__ = (UniqueConstraint("user_id", "site_id", name="uq_site_grants_user_site"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    site_id: Mapped[str | None] = mapped_column(
        ForeignKey("locations.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditEntry(Base):
    """Append-only audit trail. Rows are only ever inserted, never changed.

    Nothing in the code base updates or deletes these rows, and the API only
    exposes reads. ``scope`` records the authorization scope a domain write
    was covered by ("installation" or "site:<id>"); it is null for actions
    that carry no site-scope check, such as user and token management.
    """

    __tablename__ = "audit_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    actor: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
