"""Pydantic request and response schemas for the v1 API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .domain import (
    AssetCriticality,
    AssetStatus,
    CommissioningTestStatus,
    ConstraintKind,
    ConstraintStatus,
    IncidentSeverity,
    IncidentStatus,
    LocationKind,
    MaintenanceOrderStatus,
    MaintenanceOrderType,
    ProcedureKind,
    ProcedureStatus,
    PunchItemCategory,
    PunchItemSeverity,
    PunchItemStatus,
    UserRole,
    WorkPermitStatus,
)


class Page[T](BaseModel):
    """Envelope for paginated list responses."""

    items: list[T]
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    database: Literal["ok"]


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
        description="Lowercase slug: letters, digits, and inner hyphens.",
    )
    display_name: str = Field(min_length=1, max_length=255)
    role: UserRole
    site_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Sites the user's write authority is limited to; omit for an installation-wide grant."
        ),
    )


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ApiTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=128)
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiry; must lie in the future. Naive values are read as UTC.",
    )


class ApiTokenResponse(BaseModel):
    id: str
    label: str
    expires_at: datetime | None
    revoked: bool
    created_at: datetime


class ApiTokenCreatedResponse(ApiTokenResponse):
    token: str = Field(description="The token secret, shown exactly once at creation.")


class SiteGrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_id: str | None = Field(
        default=None,
        description="The covered site, or null for an installation-wide grant.",
    )


class SiteGrantResponse(BaseModel):
    id: str
    user_id: str
    site_id: str | None
    scope: str = Field(description='"installation" or "site:<id>".')
    created_at: datetime


class LocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LocationKind
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = None


class LocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=255)


class LocationResponse(BaseModel):
    id: str
    kind: LocationKind
    code: str
    name: str
    parent_id: str | None
    created_at: datetime
    updated_at: datetime


class AssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    asset_type: str = Field(
        min_length=1,
        max_length=128,
        description="Free-text asset category, for example 'ups' or 'chiller'.",
    )
    criticality: AssetCriticality
    location_id: str
    status: AssetStatus = AssetStatus.PLANNED


class AssetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    asset_type: str | None = Field(default=None, min_length=1, max_length=128)
    criticality: AssetCriticality | None = None
    location_id: str | None = None


class AssetTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_status: AssetStatus


class AssetResponse(BaseModel):
    id: str
    tag: str
    name: str
    asset_type: str
    criticality: AssetCriticality
    status: AssetStatus
    location_id: str
    site_id: str
    created_at: datetime
    updated_at: datetime


class MaintenanceOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_type: MaintenanceOrderType
    asset_id: str
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    due_date: date


class MaintenanceOrderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    due_date: date | None = None


class MaintenanceOrderComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completion_notes: str = Field(min_length=1)


class MaintenanceOrderCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class MaintenanceOrderResponse(BaseModel):
    id: str
    order_type: MaintenanceOrderType
    status: MaintenanceOrderStatus
    asset_id: str
    title: str
    description: str | None
    due_date: date
    completion_notes: str | None
    created_at: datetime
    updated_at: datetime


class ProcedureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ProcedureKind
    title: str = Field(min_length=1, max_length=255)
    steps: list[str] = Field(min_length=1)


class ProcedureUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    steps: list[str] | None = Field(default=None, min_length=1)


class ProcedureResponse(BaseModel):
    id: str
    kind: ProcedureKind
    status: ProcedureStatus
    title: str
    steps: list[str]
    version: int
    predecessor_id: str | None
    last_edited_by: str
    approved_by: str | None
    created_at: datetime
    updated_at: datetime


class WorkPermitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    procedure_id: str | None = None
    scope: str = Field(min_length=1)


class WorkPermitClose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completion_note: str = Field(min_length=1)


class WorkPermitRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class WorkPermitResponse(BaseModel):
    id: str
    order_id: str
    procedure_id: str | None
    scope: str
    status: WorkPermitStatus
    requested_by: str
    issued_by: str | None
    completion_note: str | None
    created_at: datetime
    updated_at: datetime


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    severity: IncidentSeverity
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None


class IncidentResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_notes: str = Field(min_length=1)


class IncidentDismiss(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class IncidentCorrectiveOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_date: date
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class IncidentResponse(BaseModel):
    id: str
    asset_id: str
    severity: IncidentSeverity
    status: IncidentStatus
    title: str
    description: str | None
    resolution_notes: str | None
    corrective_order_id: str | None
    created_at: datetime
    updated_at: datetime


class PunchItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str | None = None
    location_id: str | None = None
    category: PunchItemCategory
    severity: PunchItemSeverity
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    due_date: date | None = None


class PunchItemClose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    closing_note: str = Field(min_length=1)


class PunchItemResponse(BaseModel):
    id: str
    asset_id: str | None
    location_id: str | None
    category: PunchItemCategory
    severity: PunchItemSeverity
    status: PunchItemStatus
    title: str
    description: str | None
    due_date: date | None
    started_by: str | None
    verified_by: str | None
    closing_note: str | None
    created_at: datetime
    updated_at: datetime


class CommissioningTestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    procedure_id: str | None = None
    title: str = Field(min_length=1, max_length=255)
    planned_date: date


class CommissioningEvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1)


class CommissioningTestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    witness: str = Field(
        min_length=1,
        max_length=128,
        description="Username of the witness; must be an active user other than the executor.",
    )
    evidence_note: str | None = None


class CommissioningTestPunchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: PunchItemSeverity
    category: PunchItemCategory = PunchItemCategory.DEFECT
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    due_date: date | None = None


class CommissioningEvidenceResponse(BaseModel):
    id: int
    note: str
    actor: str
    recorded_at: datetime


class CommissioningTestResponse(BaseModel):
    id: str
    asset_id: str
    procedure_id: str | None
    title: str
    planned_date: date
    status: CommissioningTestStatus
    executed_by: str | None
    witnessed_by: str | None
    punch_item_id: str | None
    evidence: list[CommissioningEvidenceResponse]
    created_at: datetime
    updated_at: datetime


class EvidenceObjectResponse(BaseModel):
    id: str
    sha256: str = Field(
        description="SHA-256 of the stored content, computed server-side during upload."
    )
    size_bytes: int
    filename: str = Field(
        description="Filename declared when this content first entered the store."
    )
    content_type: str
    uploaded_by: str
    created_at: datetime


class EvidenceAttachmentResponse(BaseModel):
    id: int
    evidence_object: EvidenceObjectResponse
    commissioning_test_id: str | None
    punch_item_id: str | None
    order_id: str | None
    permit_id: str | None
    note: str | None
    attached_by: str
    attached_at: datetime


class ConstraintCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ConstraintKind
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    asset_ids: list[str] = Field(min_length=1)


class ConstraintResponse(BaseModel):
    id: str
    kind: ConstraintKind
    name: str
    description: str | None
    status: ConstraintStatus
    asset_ids: list[str]
    created_at: datetime
    updated_at: datetime


class AuditEntryResponse(BaseModel):
    id: int
    occurred_at: datetime
    actor: str
    entity_type: str
    entity_id: str
    action: str
    scope: str | None = Field(
        description=(
            'The authorization scope that covered the write ("installation" or '
            '"site:<id>"); null for actions that carry no site-scope check.'
        )
    )
    before: dict[str, Any] | None
    after: dict[str, Any] | None
