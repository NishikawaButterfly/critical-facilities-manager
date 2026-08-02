"""Domain vocabulary: enumerations, state machines, and hierarchy rules."""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """What an authenticated user is allowed to do across the API.

    Viewers may only read, engineers additionally run every domain workflow,
    and admins additionally manage users and API tokens.
    """

    VIEWER = "viewer"
    ENGINEER = "engineer"
    ADMIN = "admin"


class LocationKind(StrEnum):
    """Levels of the physical location hierarchy, from largest to smallest."""

    SITE = "site"
    BUILDING = "building"
    FLOOR = "floor"
    ROOM = "room"


LOCATION_PARENT_KIND: dict[LocationKind, LocationKind | None] = {
    LocationKind.SITE: None,
    LocationKind.BUILDING: LocationKind.SITE,
    LocationKind.FLOOR: LocationKind.BUILDING,
    LocationKind.ROOM: LocationKind.FLOOR,
}
"""Required parent kind for each location kind (the adjacency rule)."""


class AssetCriticality(StrEnum):
    """How much a facility depends on the asset staying available."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AssetStatus(StrEnum):
    """Lifecycle status of an asset."""

    PLANNED = "planned"
    INSTALLED = "installed"
    OPERATIONAL = "operational"
    UNDER_MAINTENANCE = "under_maintenance"
    DECOMMISSIONED = "decommissioned"


ASSET_TRANSITIONS: dict[AssetStatus, frozenset[AssetStatus]] = {
    AssetStatus.PLANNED: frozenset({AssetStatus.INSTALLED}),
    AssetStatus.INSTALLED: frozenset({AssetStatus.OPERATIONAL}),
    AssetStatus.OPERATIONAL: frozenset({AssetStatus.UNDER_MAINTENANCE, AssetStatus.DECOMMISSIONED}),
    AssetStatus.UNDER_MAINTENANCE: frozenset({AssetStatus.OPERATIONAL, AssetStatus.DECOMMISSIONED}),
    AssetStatus.DECOMMISSIONED: frozenset(),
}
"""Allowed asset status transitions. Anything not listed is rejected."""


class MaintenanceOrderType(StrEnum):
    """Whether the work is planned upkeep or a repair."""

    PREVENTIVE = "preventive"
    CORRECTIVE = "corrective"


class MaintenanceOrderStatus(StrEnum):
    """Workflow status of a maintenance order."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


ORDER_TRANSITIONS: dict[MaintenanceOrderStatus, frozenset[MaintenanceOrderStatus]] = {
    MaintenanceOrderStatus.DRAFT: frozenset(
        {MaintenanceOrderStatus.SCHEDULED, MaintenanceOrderStatus.CANCELLED}
    ),
    MaintenanceOrderStatus.SCHEDULED: frozenset(
        {MaintenanceOrderStatus.IN_PROGRESS, MaintenanceOrderStatus.CANCELLED}
    ),
    MaintenanceOrderStatus.IN_PROGRESS: frozenset(
        {MaintenanceOrderStatus.DONE, MaintenanceOrderStatus.CANCELLED}
    ),
    MaintenanceOrderStatus.DONE: frozenset(),
    MaintenanceOrderStatus.CANCELLED: frozenset(),
}
"""Allowed maintenance order transitions. Done and cancelled are terminal."""


class ProcedureKind(StrEnum):
    """Categories of written operating procedures."""

    MOP = "mop"
    SOP = "sop"
    EOP = "eop"


class ProcedureStatus(StrEnum):
    """Version lifecycle of a procedure."""

    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


PROCEDURE_TRANSITIONS: dict[ProcedureStatus, frozenset[ProcedureStatus]] = {
    ProcedureStatus.DRAFT: frozenset({ProcedureStatus.APPROVED}),
    ProcedureStatus.APPROVED: frozenset({ProcedureStatus.RETIRED}),
    ProcedureStatus.RETIRED: frozenset(),
}
"""Allowed procedure status transitions. Retired is terminal."""


class WorkPermitStatus(StrEnum):
    """Workflow status of a work permit."""

    REQUESTED = "requested"
    ISSUED = "issued"
    CLOSED = "closed"
    REVOKED = "revoked"


PERMIT_TRANSITIONS: dict[WorkPermitStatus, frozenset[WorkPermitStatus]] = {
    WorkPermitStatus.REQUESTED: frozenset({WorkPermitStatus.ISSUED}),
    WorkPermitStatus.ISSUED: frozenset({WorkPermitStatus.CLOSED, WorkPermitStatus.REVOKED}),
    WorkPermitStatus.CLOSED: frozenset(),
    WorkPermitStatus.REVOKED: frozenset(),
}
"""Allowed work permit transitions. Closed and revoked are terminal."""


class IncidentSeverity(StrEnum):
    """Operational impact of an incident."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    """Workflow status of an incident."""

    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


INCIDENT_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.OPEN: frozenset({IncidentStatus.INVESTIGATING}),
    IncidentStatus.INVESTIGATING: frozenset({IncidentStatus.RESOLVED, IncidentStatus.DISMISSED}),
    IncidentStatus.RESOLVED: frozenset(),
    IncidentStatus.DISMISSED: frozenset(),
}
"""Allowed incident transitions. Resolved and dismissed are terminal."""


class PunchItemCategory(StrEnum):
    """What kind of problem a punch item records."""

    DEFECT = "defect"
    MISSING = "missing"
    DOCUMENTATION = "documentation"
    SAFETY = "safety"


class PunchItemSeverity(StrEnum):
    """How much a punch item blocks acceptance of the work."""

    MINOR = "minor"
    MAJOR = "major"
    BLOCKING = "blocking"


class PunchItemStatus(StrEnum):
    """Workflow status of a punch item."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


PUNCH_ITEM_TRANSITIONS: dict[PunchItemStatus, frozenset[PunchItemStatus]] = {
    PunchItemStatus.OPEN: frozenset({PunchItemStatus.IN_PROGRESS}),
    PunchItemStatus.IN_PROGRESS: frozenset({PunchItemStatus.CLOSED}),
    PunchItemStatus.CLOSED: frozenset(),
}
"""Allowed punch item transitions. Closed is terminal."""


class CommissioningTestStatus(StrEnum):
    """Workflow status of a commissioning test."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


COMMISSIONING_TRANSITIONS: dict[CommissioningTestStatus, frozenset[CommissioningTestStatus]] = {
    CommissioningTestStatus.PENDING: frozenset(
        {CommissioningTestStatus.PASSED, CommissioningTestStatus.FAILED}
    ),
    CommissioningTestStatus.PASSED: frozenset(),
    CommissioningTestStatus.FAILED: frozenset(),
}
"""Allowed commissioning test transitions. Passed and failed are terminal."""


class ConstraintKind(StrEnum):
    """How a constraint over a set of member assets behaves.

    ``mutual_exclusive_maintenance`` is enforced when maintenance orders
    start; ``advisory`` is free text that is only surfaced, never enforced.
    """

    MUTUAL_EXCLUSIVE_MAINTENANCE = "mutual_exclusive_maintenance"
    ADVISORY = "advisory"


class ConstraintStatus(StrEnum):
    """Lifecycle of a constraint. Constraints are created and retired, never edited."""

    ACTIVE = "active"
    RETIRED = "retired"


CONSTRAINT_TRANSITIONS: dict[ConstraintStatus, frozenset[ConstraintStatus]] = {
    ConstraintStatus.ACTIVE: frozenset({ConstraintStatus.RETIRED}),
    ConstraintStatus.RETIRED: frozenset(),
}
"""Allowed constraint transitions. Retired is terminal."""
