"""Synthetic demo dataset so the API is explorable immediately after seeding.

Every code, name, and figure here is invented. The fictional campus follows the
generic "Building C" naming style used across this project's examples.

The seed also creates a small synthetic crew (one admin, two engineers, one
viewer) and runs every workflow action as one of those users, so the audit
trail shows real actors. One API token is minted per user; the plaintext
secrets are returned by :func:`seed_demo` (and printed once by the seed
script) because only their hashes are stored — this is also how the tests get
deterministic access to the seeded API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import Base, build_engine, build_session_factory
from .domain import (
    AssetCriticality,
    AssetStatus,
    CommissioningTestStatus,
    ConstraintKind,
    IncidentSeverity,
    IncidentStatus,
    LocationKind,
    MaintenanceOrderStatus,
    MaintenanceOrderType,
    ProcedureKind,
    PunchItemCategory,
    PunchItemSeverity,
    UserRole,
    WorkPermitStatus,
)
from .models import (
    ApiToken,
    Asset,
    AuditEntry,
    CommissioningTest,
    Constraint,
    Incident,
    Location,
    MaintenanceOrder,
    Procedure,
    PunchItem,
    User,
    WorkPermit,
)
from .services.assets import create_asset, transition_asset
from .services.commissioning import (
    add_evidence,
    create_punch_item_from_test,
    create_test,
    record_result,
)
from .services.constraints import create_constraint
from .services.incidents import (
    create_corrective_order,
    create_incident,
    transition_incident,
)
from .services.locations import create_location
from .services.maintenance import create_order, transition_order
from .services.permits import create_permit, transition_permit
from .services.procedures import (
    approve_procedure,
    create_new_version,
    create_procedure,
    update_procedure,
)
from .services.punch_items import create_punch_item
from .services.tokens import create_token
from .services.users import create_user

SEED_ADMIN = "rowan-admin"
SEED_ENGINEER = "priya-eng"
SEED_REVIEWER = "marco-eng"
SEED_VIEWER = "vera-viewer"

SEED_CREW: tuple[tuple[str, str, UserRole], ...] = (
    (SEED_ADMIN, "Rowan Ellis", UserRole.ADMIN),
    (SEED_ENGINEER, "Priya Sharma", UserRole.ENGINEER),
    (SEED_REVIEWER, "Marco Alvarez", UserRole.ENGINEER),
    (SEED_VIEWER, "Vera Lindqvist", UserRole.VIEWER),
)


class SeedError(RuntimeError):
    """Raised when the target database already contains data."""


@dataclass(frozen=True)
class SeedResult:
    """Row counts per table plus the crew's token secrets, shown exactly once."""

    counts: dict[str, int]
    tokens: dict[str, str]


def _count(session: Session, model: type[Base]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _seed_crew(session: Session) -> dict[str, str]:
    """Create the synthetic crew and one demo token per user.

    The admin is recorded as the creator of every account, its own included —
    the same shape a bootstrap plus API-driven setup would leave behind.
    """
    tokens: dict[str, str] = {}
    for username, display_name, role in SEED_CREW:
        user = create_user(
            session,
            SEED_ADMIN,
            username=username,
            display_name=display_name,
            role=role,
        )
        _, secret = create_token(session, SEED_ADMIN, user, label="demo", expires_at=None)
        tokens[username] = secret
    return tokens


def _seed(session: Session) -> None:
    today = datetime.now(UTC).date()

    site = create_location(
        session,
        SEED_ENGINEER,
        kind=LocationKind.SITE,
        code="CAMP-A",
        name="Camellia Demo Campus",
        parent_id=None,
    )
    building = create_location(
        session,
        SEED_ENGINEER,
        kind=LocationKind.BUILDING,
        code="BLDG-C",
        name="Building C - Data Hall Block",
        parent_id=site.id,
    )
    level_1 = create_location(
        session,
        SEED_ENGINEER,
        kind=LocationKind.FLOOR,
        code="L1",
        name="Level 1",
        parent_id=building.id,
    )
    level_2 = create_location(
        session,
        SEED_ENGINEER,
        kind=LocationKind.FLOOR,
        code="L2",
        name="Level 2",
        parent_id=building.id,
    )
    electrical_room = create_location(
        session,
        SEED_ENGINEER,
        kind=LocationKind.ROOM,
        code="L1-ELEC",
        name="Electrical Room 1A",
        parent_id=level_1.id,
    )
    data_hall = create_location(
        session,
        SEED_ENGINEER,
        kind=LocationKind.ROOM,
        code="L1-DH1",
        name="Data Hall 1",
        parent_id=level_1.id,
    )
    plant_room = create_location(
        session,
        SEED_ENGINEER,
        kind=LocationKind.ROOM,
        code="L2-MECH",
        name="Mechanical Plant Room",
        parent_id=level_2.id,
    )

    ups = create_asset(
        session,
        SEED_ENGINEER,
        tag="UPS-C-01",
        name="UPS System 1",
        asset_type="ups",
        criticality=AssetCriticality.CRITICAL,
        location_id=electrical_room.id,
        status=AssetStatus.OPERATIONAL,
    )
    ups_2 = create_asset(
        session,
        SEED_ENGINEER,
        tag="UPS-C-02",
        name="UPS System 2",
        asset_type="ups",
        criticality=AssetCriticality.CRITICAL,
        location_id=electrical_room.id,
        status=AssetStatus.OPERATIONAL,
    )
    generator = create_asset(
        session,
        SEED_ENGINEER,
        tag="GEN-C-01",
        name="Standby Generator 1",
        asset_type="generator",
        criticality=AssetCriticality.CRITICAL,
        location_id=building.id,
        status=AssetStatus.OPERATIONAL,
    )
    crac_1 = create_asset(
        session,
        SEED_ENGINEER,
        tag="CRAC-C-01",
        name="CRAC Unit 1",
        asset_type="crac",
        criticality=AssetCriticality.HIGH,
        location_id=data_hall.id,
        status=AssetStatus.OPERATIONAL,
    )
    crac_2 = create_asset(
        session,
        SEED_ENGINEER,
        tag="CRAC-C-02",
        name="CRAC Unit 2",
        asset_type="crac",
        criticality=AssetCriticality.HIGH,
        location_id=data_hall.id,
        status=AssetStatus.PLANNED,
    )
    chiller = create_asset(
        session,
        SEED_ENGINEER,
        tag="CHW-C-01",
        name="Chiller 1",
        asset_type="chiller",
        criticality=AssetCriticality.HIGH,
        location_id=plant_room.id,
        status=AssetStatus.OPERATIONAL,
    )
    pdu = create_asset(
        session,
        SEED_ENGINEER,
        tag="PDU-C-01",
        name="PDU 1A",
        asset_type="pdu",
        criticality=AssetCriticality.MEDIUM,
        location_id=data_hall.id,
        status=AssetStatus.OPERATIONAL,
    )

    # CRAC unit 2 is being brought into service: planned -> installed.
    transition_asset(session, SEED_ENGINEER, crac_2, AssetStatus.INSTALLED)

    ups_inspection = create_order(
        session,
        SEED_ENGINEER,
        order_type=MaintenanceOrderType.PREVENTIVE,
        asset_id=ups.id,
        title="Quarterly UPS inspection",
        description="Battery string voltages, transfer test, capacitor check.",
        due_date=today + timedelta(days=30),
    )
    transition_order(session, SEED_ENGINEER, ups_inspection, MaintenanceOrderStatus.SCHEDULED)

    fan_repair = create_order(
        session,
        SEED_ENGINEER,
        order_type=MaintenanceOrderType.CORRECTIVE,
        asset_id=crac_1.id,
        title="Replace failed fan module",
        description="Fan 2 reports zero RPM; unit running on redundant fans.",
        due_date=today + timedelta(days=7),
    )
    transition_order(session, SEED_ENGINEER, fan_repair, MaintenanceOrderStatus.SCHEDULED)
    transition_order(session, SEED_ENGINEER, fan_repair, MaintenanceOrderStatus.IN_PROGRESS)

    chiller_service = create_order(
        session,
        SEED_ENGINEER,
        order_type=MaintenanceOrderType.PREVENTIVE,
        asset_id=chiller.id,
        title="Annual chiller service",
        description="Condenser cleaning, refrigerant charge check, vibration survey.",
        due_date=today - timedelta(days=3),
    )
    transition_order(session, SEED_ENGINEER, chiller_service, MaintenanceOrderStatus.SCHEDULED)
    transition_order(session, SEED_ENGINEER, chiller_service, MaintenanceOrderStatus.IN_PROGRESS)
    transition_order(
        session,
        SEED_ENGINEER,
        chiller_service,
        MaintenanceOrderStatus.DONE,
        notes="Service completed; all parameters nominal.",
    )

    create_order(
        session,
        SEED_ENGINEER,
        order_type=MaintenanceOrderType.PREVENTIVE,
        asset_id=pdu.id,
        title="PDU thermal scan",
        description=None,
        due_date=today + timedelta(days=60),
    )

    duplicate_check = create_order(
        session,
        SEED_ENGINEER,
        order_type=MaintenanceOrderType.CORRECTIVE,
        asset_id=generator.id,
        title="Investigate coolant leak alarm",
        description="Raised twice; suspected faulty sensor.",
        due_date=today + timedelta(days=14),
    )
    transition_order(
        session,
        SEED_ENGINEER,
        duplicate_check,
        MaintenanceOrderStatus.CANCELLED,
        notes="Duplicate of an existing work order.",
    )

    # An approved MOP backing the permit on the fan repair.
    fan_mop = create_procedure(
        session,
        SEED_ENGINEER,
        kind=ProcedureKind.MOP,
        title="CRAC fan module replacement",
        steps=[
            "Notify operations and confirm redundant cooling capacity.",
            "Isolate and lock out the fan module power feed.",
            "Swap the fan module and torque the fasteners to specification.",
            "Restore power, verify RPM and airflow, and close out with operations.",
        ],
    )
    approve_procedure(session, SEED_REVIEWER, fan_mop)

    # An approved SOP with a version chain: v1 approved, v2 back in draft.
    access_sop_v1 = create_procedure(
        session,
        SEED_ENGINEER,
        kind=ProcedureKind.SOP,
        title="Data hall access and escort",
        steps=[
            "Verify the visitor is on the approved access list.",
            "Issue a temporary badge and log the entry time.",
            "Escort the visitor at all times inside the data hall.",
        ],
    )
    approve_procedure(session, SEED_REVIEWER, access_sop_v1)
    access_sop_v2 = create_new_version(session, SEED_ENGINEER, access_sop_v1)
    update_procedure(
        session,
        SEED_ENGINEER,
        access_sop_v2,
        {
            "steps": [
                "Verify the visitor is on the approved access list.",
                "Issue a temporary badge and log the entry time.",
                "Escort the visitor at all times inside the data hall.",
                "Collect the badge and log the exit time on the way out.",
            ]
        },
    )

    # An emergency procedure still being drafted.
    create_procedure(
        session,
        SEED_ENGINEER,
        kind=ProcedureKind.EOP,
        title="Loss of cooling emergency response",
        steps=[
            "Confirm the alarm and identify the affected cooling units.",
            "Start the standby units and open the emergency airflow paths.",
            "Escalate to the on-call facilities manager.",
        ],
    )

    # An issued permit on the in-progress fan repair, under the approved MOP.
    fan_permit = create_permit(
        session,
        SEED_ENGINEER,
        order_id=fan_repair.id,
        procedure_id=fan_mop.id,
        scope="Replace fan module 2 of CRAC unit 1; work confined to the unit enclosure.",
    )
    transition_permit(session, SEED_REVIEWER, fan_permit, WorkPermitStatus.ISSUED)

    # A second permit still waiting to be issued.
    create_permit(
        session,
        SEED_ENGINEER,
        order_id=ups_inspection.id,
        procedure_id=None,
        scope="Quarterly UPS inspection incl. transfer test in Electrical Room 1A.",
    )

    # A high-severity incident under investigation, linked to a corrective order.
    pressure_incident = create_incident(
        session,
        SEED_ENGINEER,
        asset_id=chiller.id,
        severity=IncidentSeverity.HIGH,
        title="Chiller tripped on high discharge pressure",
        description="Unit restarted after lockout reset; root cause not confirmed.",
    )
    transition_incident(session, SEED_ENGINEER, pressure_incident, IncidentStatus.INVESTIGATING)
    create_corrective_order(
        session,
        SEED_ENGINEER,
        pressure_incident,
        due_date=today + timedelta(days=10),
        title="Inspect chiller high-pressure trip",
        description="Check condenser water flow, fouling, and the discharge pressure sensor.",
    )

    # A low-severity incident that has just been reported.
    create_incident(
        session,
        SEED_ENGINEER,
        asset_id=pdu.id,
        severity=IncidentSeverity.LOW,
        title="Branch circuit breaker labeled incorrectly",
        description=None,
    )

    _seed_commissioning(
        session,
        today=today,
        ups=ups,
        ups_2=ups_2,
        crac_1=crac_1,
        crac_2=crac_2,
        data_hall=data_hall,
    )


def _seed_commissioning(
    session: Session,
    *,
    today: date,
    ups: Asset,
    ups_2: Asset,
    crac_1: Asset,
    crac_2: Asset,
    data_hall: Location,
) -> None:
    """Commissioning tests, punch items, and constraints for the campus."""
    # CRAC unit 2 is under commissioning: an airflow test passed with
    # witnessed evidence, and a condensate alarm test failed and opened a
    # punch item on the unit.
    airflow_test = create_test(
        session,
        SEED_ENGINEER,
        asset_id=crac_2.id,
        procedure_id=None,
        title="CRAC unit 2 airflow verification",
        planned_date=today - timedelta(days=2),
    )
    add_evidence(
        session,
        SEED_ENGINEER,
        airflow_test,
        "Supply air temperature held the set point for 30 minutes.",
    )
    add_evidence(
        session,
        SEED_ENGINEER,
        airflow_test,
        "Measured airflow within 5 percent of the design value.",
    )
    record_result(
        session,
        SEED_ENGINEER,
        airflow_test,
        CommissioningTestStatus.PASSED,
        witness=SEED_REVIEWER,
        evidence_note="Witnessed the transfer to normal operation; no alarms raised.",
    )

    alarm_test = create_test(
        session,
        SEED_ENGINEER,
        asset_id=crac_2.id,
        procedure_id=None,
        title="CRAC unit 2 condensate alarm test",
        planned_date=today - timedelta(days=1),
    )
    record_result(
        session,
        SEED_ENGINEER,
        alarm_test,
        CommissioningTestStatus.FAILED,
        witness=SEED_REVIEWER,
        evidence_note="Simulated condensate overflow never raised the alarm at the head end.",
    )
    create_punch_item_from_test(
        session,
        SEED_ENGINEER,
        alarm_test,
        category=PunchItemCategory.DEFECT,
        severity=PunchItemSeverity.MAJOR,
        title=None,
        description="Float switch wiring suspected; the alarm never reached monitoring.",
        due_date=today + timedelta(days=14),
    )

    # A blocking documentation gap on the data hall, already overdue.
    create_punch_item(
        session,
        SEED_ENGINEER,
        asset_id=None,
        location_id=data_hall.id,
        category=PunchItemCategory.DOCUMENTATION,
        severity=PunchItemSeverity.BLOCKING,
        title="As-built cable schedule missing for Data Hall 1",
        description="The handover pack lacks the final cable schedule; acceptance is blocked.",
        due_date=today - timedelta(days=5),
    )

    # The redundant UPS pair must never be under maintenance at the same
    # time. The seed's orders respect this: the UPS inspection is scheduled,
    # not started. A second, advisory constraint is only surfaced.
    create_constraint(
        session,
        SEED_ENGINEER,
        kind=ConstraintKind.MUTUAL_EXCLUSIVE_MAINTENANCE,
        name="UPS redundancy pair",
        description="UPS 1 and UPS 2 back each other; only one may be worked on at a time.",
        asset_ids=[ups.id, ups_2.id],
    )
    create_constraint(
        session,
        SEED_ENGINEER,
        kind=ConstraintKind.ADVISORY,
        name="CRAC maintenance window",
        description="Prefer maintaining the CRAC units outside the summer peak window.",
        asset_ids=[crac_1.id, crac_2.id],
    )


def seed_demo(database_url: str | None = None) -> SeedResult:
    """Seed the demo dataset and return row counts plus the crew's tokens."""
    url = database_url or get_settings().database_url
    engine = build_engine(url)
    try:
        Base.metadata.create_all(engine)
        session_factory = build_session_factory(engine)
        with session_factory() as session:
            if _count(session, Location) or _count(session, User):
                raise SeedError(
                    "The database already contains locations or users; refusing "
                    "to seed on top of existing data."
                )
            tokens = _seed_crew(session)
            _seed(session)
            counts = {
                "users": _count(session, User),
                "api_tokens": _count(session, ApiToken),
                "locations": _count(session, Location),
                "assets": _count(session, Asset),
                "maintenance_orders": _count(session, MaintenanceOrder),
                "procedures": _count(session, Procedure),
                "work_permits": _count(session, WorkPermit),
                "incidents": _count(session, Incident),
                "commissioning_tests": _count(session, CommissioningTest),
                "punch_items": _count(session, PunchItem),
                "constraints": _count(session, Constraint),
                "audit_entries": _count(session, AuditEntry),
            }
            return SeedResult(counts=counts, tokens=tokens)
    finally:
        engine.dispose()
