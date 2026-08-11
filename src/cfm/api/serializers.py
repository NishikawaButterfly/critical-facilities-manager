"""ORM-to-response conversion helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from ..authz import scope_label
from ..domain import (
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
from ..models import (
    ApiToken,
    Asset,
    AuditEntry,
    CommissioningEvidence,
    CommissioningTest,
    Constraint,
    Incident,
    Location,
    MaintenanceOrder,
    Procedure,
    PunchItem,
    SiteGrant,
    User,
    WorkPermit,
)
from ..schemas import (
    ApiTokenCreatedResponse,
    ApiTokenResponse,
    AssetResponse,
    AuditEntryResponse,
    CommissioningEvidenceResponse,
    CommissioningTestResponse,
    ConstraintResponse,
    IncidentResponse,
    LocationResponse,
    MaintenanceOrderResponse,
    ProcedureResponse,
    PunchItemResponse,
    SiteGrantResponse,
    UserResponse,
    WorkPermitResponse,
)


def ensure_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; make every timestamp explicitly UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=UserRole(user.role),
        is_active=user.is_active,
        created_at=ensure_utc(user.created_at),
        updated_at=ensure_utc(user.updated_at),
    )


def api_token_response(token: ApiToken) -> ApiTokenResponse:
    return ApiTokenResponse(
        id=token.id,
        label=token.label,
        expires_at=ensure_utc(token.expires_at) if token.expires_at is not None else None,
        revoked=token.revoked,
        created_at=ensure_utc(token.created_at),
    )


def api_token_created_response(token: ApiToken, secret: str) -> ApiTokenCreatedResponse:
    """The only serializer that ever sees a token secret, used exactly once."""
    return ApiTokenCreatedResponse(
        id=token.id,
        label=token.label,
        expires_at=ensure_utc(token.expires_at) if token.expires_at is not None else None,
        revoked=token.revoked,
        created_at=ensure_utc(token.created_at),
        token=secret,
    )


def site_grant_response(grant: SiteGrant) -> SiteGrantResponse:
    return SiteGrantResponse(
        id=grant.id,
        user_id=grant.user_id,
        site_id=grant.site_id,
        scope=scope_label(grant.site_id),
        created_at=ensure_utc(grant.created_at),
    )


def location_response(location: Location) -> LocationResponse:
    return LocationResponse(
        id=location.id,
        kind=LocationKind(location.kind),
        code=location.code,
        name=location.name,
        parent_id=location.parent_id,
        created_at=ensure_utc(location.created_at),
        updated_at=ensure_utc(location.updated_at),
    )


def asset_response(asset: Asset) -> AssetResponse:
    return AssetResponse(
        id=asset.id,
        tag=asset.tag,
        name=asset.name,
        asset_type=asset.asset_type,
        criticality=AssetCriticality(asset.criticality),
        status=AssetStatus(asset.status),
        location_id=asset.location_id,
        site_id=asset.site_id,
        created_at=ensure_utc(asset.created_at),
        updated_at=ensure_utc(asset.updated_at),
    )


def order_response(order: MaintenanceOrder) -> MaintenanceOrderResponse:
    return MaintenanceOrderResponse(
        id=order.id,
        order_type=MaintenanceOrderType(order.order_type),
        status=MaintenanceOrderStatus(order.status),
        asset_id=order.asset_id,
        title=order.title,
        description=order.description,
        due_date=order.due_date,
        completion_notes=order.completion_notes,
        created_at=ensure_utc(order.created_at),
        updated_at=ensure_utc(order.updated_at),
    )


def procedure_response(procedure: Procedure) -> ProcedureResponse:
    return ProcedureResponse(
        id=procedure.id,
        kind=ProcedureKind(procedure.kind),
        status=ProcedureStatus(procedure.status),
        title=procedure.title,
        steps=list(procedure.steps),
        version=procedure.version,
        predecessor_id=procedure.predecessor_id,
        last_edited_by=procedure.last_edited_by,
        approved_by=procedure.approved_by,
        created_at=ensure_utc(procedure.created_at),
        updated_at=ensure_utc(procedure.updated_at),
    )


def work_permit_response(permit: WorkPermit) -> WorkPermitResponse:
    return WorkPermitResponse(
        id=permit.id,
        order_id=permit.order_id,
        procedure_id=permit.procedure_id,
        scope=permit.scope,
        status=WorkPermitStatus(permit.status),
        requested_by=permit.requested_by,
        issued_by=permit.issued_by,
        completion_note=permit.completion_note,
        created_at=ensure_utc(permit.created_at),
        updated_at=ensure_utc(permit.updated_at),
    )


def incident_response(incident: Incident) -> IncidentResponse:
    return IncidentResponse(
        id=incident.id,
        asset_id=incident.asset_id,
        severity=IncidentSeverity(incident.severity),
        status=IncidentStatus(incident.status),
        title=incident.title,
        description=incident.description,
        resolution_notes=incident.resolution_notes,
        corrective_order_id=incident.corrective_order_id,
        created_at=ensure_utc(incident.created_at),
        updated_at=ensure_utc(incident.updated_at),
    )


def punch_item_response(item: PunchItem) -> PunchItemResponse:
    return PunchItemResponse(
        id=item.id,
        asset_id=item.asset_id,
        location_id=item.location_id,
        category=PunchItemCategory(item.category),
        severity=PunchItemSeverity(item.severity),
        status=PunchItemStatus(item.status),
        title=item.title,
        description=item.description,
        due_date=item.due_date,
        started_by=item.started_by,
        verified_by=item.verified_by,
        closing_note=item.closing_note,
        created_at=ensure_utc(item.created_at),
        updated_at=ensure_utc(item.updated_at),
    )


def commissioning_evidence_response(entry: CommissioningEvidence) -> CommissioningEvidenceResponse:
    return CommissioningEvidenceResponse(
        id=entry.id,
        note=entry.note,
        actor=entry.actor,
        recorded_at=ensure_utc(entry.recorded_at),
    )


def commissioning_test_response(test: CommissioningTest) -> CommissioningTestResponse:
    return CommissioningTestResponse(
        id=test.id,
        asset_id=test.asset_id,
        procedure_id=test.procedure_id,
        title=test.title,
        planned_date=test.planned_date,
        status=CommissioningTestStatus(test.status),
        executed_by=test.executed_by,
        witnessed_by=test.witnessed_by,
        punch_item_id=test.punch_item_id,
        evidence=[commissioning_evidence_response(entry) for entry in test.evidence],
        created_at=ensure_utc(test.created_at),
        updated_at=ensure_utc(test.updated_at),
    )


def constraint_response(constraint: Constraint) -> ConstraintResponse:
    return ConstraintResponse(
        id=constraint.id,
        kind=ConstraintKind(constraint.kind),
        name=constraint.name,
        description=constraint.description,
        status=ConstraintStatus(constraint.status),
        asset_ids=[member.asset_id for member in constraint.members],
        created_at=ensure_utc(constraint.created_at),
        updated_at=ensure_utc(constraint.updated_at),
    )


def audit_entry_response(entry: AuditEntry) -> AuditEntryResponse:
    return AuditEntryResponse(
        id=entry.id,
        occurred_at=ensure_utc(entry.occurred_at),
        actor=entry.actor,
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        action=entry.action,
        scope=entry.scope,
        before=entry.before,
        after=entry.after,
    )
