"""Versioned REST API routers.

Role authorization is applied here, at include time, in one layer:
``/health`` stays public for probes, ``/me`` needs any authenticated user,
the domain routers admit any authenticated reader but only engineers and
admins for writes, and user/token management requires the admin role. How
far that authority reaches is the other half of the question, and the
endpoints answer it with the caller's site grants, reads included (see
:mod:`cfm.authz`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .assets import router as assets_router
from .audit import router as audit_router
from .commissioning import router as commissioning_router
from .constraints import router as constraints_router
from .deps import get_current_user, require_admin, require_domain_access
from .evidence import router as evidence_router
from .incidents import router as incidents_router
from .locations import router as locations_router
from .maintenance import router as maintenance_router
from .me import router as me_router
from .permits import router as permits_router
from .procedures import router as procedures_router
from .punch_items import router as punch_items_router
from .system import router as system_router
from .users import router as users_router

router = APIRouter()
router.include_router(system_router)

authenticated_router = APIRouter(dependencies=[Depends(get_current_user)])
authenticated_router.include_router(me_router)
router.include_router(authenticated_router)

domain_router = APIRouter(dependencies=[Depends(require_domain_access)])
domain_router.include_router(locations_router)
domain_router.include_router(assets_router)
domain_router.include_router(maintenance_router)
domain_router.include_router(procedures_router)
domain_router.include_router(permits_router)
domain_router.include_router(incidents_router)
domain_router.include_router(commissioning_router)
domain_router.include_router(punch_items_router)
domain_router.include_router(constraints_router)
domain_router.include_router(evidence_router)
domain_router.include_router(audit_router)
router.include_router(domain_router)

admin_router = APIRouter(dependencies=[Depends(require_admin)])
admin_router.include_router(users_router)
router.include_router(admin_router)
