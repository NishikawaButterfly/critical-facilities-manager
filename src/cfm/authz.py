"""Site-scoped authorization for domain reads and writes.

A user's role says *what* they may do (viewer, engineer, admin); their site
grants say *where* that authority reaches — reads included. Every domain
object resolves to the site it belongs to: assets carry a derived
``site_id``, locations walk up the tree, orders, permits, incidents, tests,
and punch items follow their asset or location, evidence follows whatever
it is attached to, and an audit entry carries the scope recorded when the
write happened. A write requires a grant covering that site; a read only
sees rows a grant covers, and an object outside the grants answers the same
404 a missing one does, so an id that exists out of scope cannot be told
apart from an id that never existed.

Objects that belong to no single site — procedures, constraints, sites
themselves — require an installation-wide grant to write and stay readable
to any authenticated user, because the site-scoped workflows refer to them:
the procedure a permit names, the constraint a refusal blames.

Write checks run inside the endpoint functions, after the role gate
admitted the write and the target objects were loaded. They must stay in
that call frame: an admitted check records the scope it used in
:data:`cfm.audit.current_scope`, which the service's audit write reads from
the same request context. Read filters run inside the query instead —
before ``COUNT``, ``OFFSET``, ``LIMIT`` and serialization — so a page never
counts, skips, or reveals a row the caller may not read.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final

from sqlalchemy import ColumnElement, Select, or_, select, true
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.expression import CTE

from .audit import current_scope
from .database import Base
from .errors import PermissionDeniedError
from .models import (
    Asset,
    AuditEntry,
    CommissioningTest,
    ConstraintMember,
    EvidenceAttachment,
    EvidenceObject,
    Incident,
    Location,
    MaintenanceOrder,
    PunchItem,
    SiteGrant,
    User,
    WorkPermit,
)
from .services.locations import resolve_site_id

INSTALLATION_SCOPE = "installation"
"""Scope label of an installation-wide grant, which covers every site."""


def site_scope(site_id: str) -> str:
    """The scope label of a grant on one site."""
    return f"site:{site_id}"


def scope_label(site_id: str | None) -> str:
    """The scope label of a grant row (``None`` meaning installation-wide)."""
    return INSTALLATION_SCOPE if site_id is None else site_scope(site_id)


def _location_sites() -> CTE:
    """Every location paired with the site at the root of its branch.

    The hierarchy is site → building → floor → room, so the recursion is at
    most four levels deep. It is expressed in SQL rather than walked in
    Python because the result is a filter on listings: it has to be part of
    the query before the count and the page window are taken.
    """
    roots = select(Location.id.label("location_id"), Location.id.label("site_id")).where(
        Location.parent_id.is_(None)
    )
    tree = roots.cte("location_sites", recursive=True)
    child = aliased(Location)
    return tree.union_all(
        select(child.id, tree.c.site_id).join(tree, child.parent_id == tree.c.location_id)
    )


def _locations_of(sites: Sequence[str]) -> Select[tuple[str]]:
    tree = _location_sites()
    return select(tree.c.location_id).where(tree.c.site_id.in_(sites))


def _assets_on(sites: Sequence[str]) -> ColumnElement[bool]:
    return Asset.site_id.in_(sites)


def _assets_of(sites: Sequence[str]) -> Select[tuple[str]]:
    return select(Asset.id).where(_assets_on(sites))


def _orders_of(sites: Sequence[str]) -> Select[tuple[str]]:
    return select(MaintenanceOrder.id).where(MaintenanceOrder.asset_id.in_(_assets_of(sites)))


def _attachments_of(sites: Sequence[str]) -> ColumnElement[bool]:
    """An attachment is readable when the object it evidences is."""
    return or_(
        EvidenceAttachment.commissioning_test_id.in_(
            select(CommissioningTest.id).where(CommissioningTest.asset_id.in_(_assets_of(sites)))
        ),
        EvidenceAttachment.punch_item_id.in_(
            select(PunchItem.id).where(
                or_(
                    PunchItem.asset_id.in_(_assets_of(sites)),
                    PunchItem.location_id.in_(_locations_of(sites)),
                )
            )
        ),
        EvidenceAttachment.order_id.in_(_orders_of(sites)),
        EvidenceAttachment.permit_id.in_(
            select(WorkPermit.id).where(WorkPermit.order_id.in_(_orders_of(sites)))
        ),
    )


READ_SCOPES: Final[dict[type[Base], Callable[[Sequence[str]], ColumnElement[bool]]]] = {
    Location: lambda sites: Location.id.in_(_locations_of(sites)),
    Asset: _assets_on,
    MaintenanceOrder: lambda sites: MaintenanceOrder.asset_id.in_(_assets_of(sites)),
    WorkPermit: lambda sites: WorkPermit.order_id.in_(_orders_of(sites)),
    Incident: lambda sites: Incident.asset_id.in_(_assets_of(sites)),
    CommissioningTest: lambda sites: CommissioningTest.asset_id.in_(_assets_of(sites)),
    PunchItem: lambda sites: or_(
        PunchItem.asset_id.in_(_assets_of(sites)),
        PunchItem.location_id.in_(_locations_of(sites)),
    ),
    ConstraintMember: lambda sites: ConstraintMember.asset_id.in_(_assets_of(sites)),
    EvidenceAttachment: _attachments_of,
    EvidenceObject: lambda sites: EvidenceObject.id.in_(
        select(EvidenceAttachment.evidence_object_id).where(_attachments_of(sites))
    ),
    # The audit trail is filtered on the scope stored when the write
    # happened, never on where the entity sits today: an asset that later
    # moved between sites must not carry its old history into the new one.
    AuditEntry: lambda sites: AuditEntry.scope.in_([site_scope(site) for site in sites]),
}
"""How each site-owned table narrows to the sites a request may read.

Every table whose rows belong to a site appears here exactly once, so the
read boundary can be read off in one place. A table that is not here is
installation-scoped by decision (procedures, constraints, users, tokens,
grants) and asking for its read filter raises :class:`KeyError` rather than
silently admitting everything.
"""


class SiteAccess:
    """Site-grant checks for one authenticated request.

    The user's grants load on the first check and are cached for the life
    of the request, so a write that checks several sites (an asset moving
    between sites) still costs a single query, and a read that filters a
    listing and then serializes it costs no more.
    """

    def __init__(self, session: Session, user: User) -> None:
        self._session = session
        self._user = user
        self._granted: frozenset[str | None] | None = None

    def _grants(self) -> frozenset[str | None]:
        if self._granted is None:
            self._granted = frozenset(
                self._session.scalars(
                    select(SiteGrant.site_id).where(SiteGrant.user_id == self._user.id)
                ).all()
            )
        return self._granted

    def require_site(self, site_id: str | None) -> None:
        """Require a grant covering ``site_id`` and record the scope used.

        A site grant is recorded over an installation-wide one when both
        cover the target; when several checks guard one write (moving an
        asset between sites), the scope recorded in the audit trail is the
        one covering the last check — the site the object ends up under.

        ``None`` means the target does not resolve to a site because a
        client-supplied reference does not exist. The check is then skipped:
        the domain layer validates the same reference and rejects the
        request before anything is written.
        """
        if site_id is None:
            return
        grants = self._grants()
        if site_id in grants:
            current_scope.set(site_scope(site_id))
            return
        if None in grants:
            current_scope.set(INSTALLATION_SCOPE)
            return
        raise PermissionDeniedError(
            f"User {self._user.username!r} holds no site grant covering site {site_id}; "
            "this write requires a grant on that site or an installation-wide grant.",
            error_code="auth.scope_forbidden",
        )

    def _readable_sites(self) -> tuple[str, ...] | None:
        """The sites this request may read, or ``None`` for every site.

        ``None`` is the installation-wide grant: it covers every site,
        present and future, so no filter is needed at all. The tuple is
        sorted to keep the generated SQL stable between requests.
        """
        grants = self._grants()
        if None in grants:
            return None
        return tuple(sorted(site_id for site_id in grants if site_id is not None))

    def readable(self, entity: type[Base]) -> ColumnElement[bool]:
        """The filter admitting only the rows of ``entity`` this request may read.

        Belongs in the query itself, next to the other filters and before
        the count and the page window, so ``total`` describes exactly the
        set the caller may read. An installation-wide grant yields a plain
        true, which costs nothing.
        """
        sites = self._readable_sites()
        if sites is None:
            return true()
        return READ_SCOPES[entity](sites)

    def require_installation(self) -> None:
        """Require an installation-wide grant, for objects no single site owns."""
        if None in self._grants():
            current_scope.set(INSTALLATION_SCOPE)
            return
        raise PermissionDeniedError(
            f"User {self._user.username!r} holds no installation-wide grant; "
            "writing objects that belong to no single site requires one.",
            error_code="auth.scope_forbidden",
        )


def site_of_location(session: Session, location: Location) -> str:
    """The site a location belongs to (a site node belongs to itself)."""
    return resolve_site_id(session, location)


def site_of_location_id(session: Session, location_id: str) -> str | None:
    location = session.get(Location, location_id)
    if location is None:
        return None
    return resolve_site_id(session, location)


def site_of_asset_id(session: Session, asset_id: str) -> str | None:
    asset = session.get(Asset, asset_id)
    return None if asset is None else asset.site_id


def site_of_order_id(session: Session, order_id: str) -> str | None:
    order = session.get(MaintenanceOrder, order_id)
    return None if order is None else site_of_asset_id(session, order.asset_id)


def site_of_punch_item(session: Session, item: PunchItem) -> str | None:
    """A punch item follows its asset or its location, whichever is set."""
    if item.asset_id is not None:
        return site_of_asset_id(session, item.asset_id)
    if item.location_id is not None:
        return site_of_location_id(session, item.location_id)
    return None  # pragma: no cover - the service enforces exactly one scope
