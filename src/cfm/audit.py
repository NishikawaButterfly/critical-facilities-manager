"""Recording helpers for the append-only audit trail."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEntry

current_scope: ContextVar[str | None] = ContextVar("cfm_audit_scope", default=None)
"""The authorization scope covering the write being handled, if any.

Set by :meth:`cfm.authz.SiteAccess` guards when they admit a write and read
back by :func:`record_audit`, so services do not need a scope parameter.
Because each request's endpoint runs in a copy of the request context, a
value set while handling one request can never leak into another; code
paths without a scope check (user management, seed scripts) record null.
"""


def jsonable(value: Any) -> Any:
    """Convert a value into something the JSON audit columns can store."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def record_audit(
    session: Session,
    *,
    actor: str,
    entity_type: str,
    entity_id: str,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> AuditEntry:
    """Append one audit entry. The caller owns the transaction."""
    entry = AuditEntry(
        actor=actor,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        scope=current_scope.get(),
        before=before,
        after=after,
    )
    session.add(entry)
    return entry
