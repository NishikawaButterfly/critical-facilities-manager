"""Recording helpers for the append-only audit trail."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEntry


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
        before=before,
        after=after,
    )
    session.add(entry)
    return entry
