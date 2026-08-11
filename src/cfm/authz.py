"""Site-scoped authorization for domain writes.

A user's role says *what* they may do (viewer, engineer, admin); their site
grants say *where* their write authority applies. Every domain write
resolves the site its target belongs to — assets carry a derived
``site_id``, locations walk up the tree, orders, permits, incidents, tests,
and punch items follow their asset or location — and requires a grant
covering that site. Objects that belong to no single site (procedures,
constraints, sites themselves) require an installation-wide grant. Reads
are not scoped.

The checks run inside the endpoint functions, after the role gate admitted
the write and the target objects were loaded. They must stay in that call
frame: an admitted check records the scope it used in
:data:`cfm.audit.current_scope`, which the service's audit write reads from
the same request context.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import current_scope
from .errors import PermissionDeniedError
from .models import Asset, Location, MaintenanceOrder, PunchItem, SiteGrant, User
from .services.locations import resolve_site_id

INSTALLATION_SCOPE = "installation"
"""Scope label of an installation-wide grant, which covers every site."""


def site_scope(site_id: str) -> str:
    """The scope label of a grant on one site."""
    return f"site:{site_id}"


def scope_label(site_id: str | None) -> str:
    """The scope label of a grant row (``None`` meaning installation-wide)."""
    return INSTALLATION_SCOPE if site_id is None else site_scope(site_id)


class SiteAccess:
    """Site-grant checks for one authenticated request.

    The user's grants load on the first check and are cached for the life
    of the request, so a write that checks several sites (an asset moving
    between sites) still costs a single query.
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
