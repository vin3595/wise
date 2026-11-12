from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditLog


def snapshot(obj: Any, fields: list[str] | None = None) -> dict:
    """JSON-safe dict of a model's columns, for before/after audit values."""
    if obj is None:
        return {}
    cols = fields or [c.key for c in obj.__table__.columns]
    out: dict[str, Any] = {}
    for key in cols:
        if key in {"pin_hash", "pin_lookup", "code_hash"}:
            continue
        value = getattr(obj, key, None)
        if isinstance(value, (uuid.UUID, datetime, date)):
            value = str(value)
        out[key] = value
    return out


def record(db: Session, tenant_id: uuid.UUID, actor_user_id: uuid.UUID | None, action: str, entity: str,
           entity_id: Any = None, before: dict | None = None, after: dict | None = None) -> None:
    db.add(AuditLog(
        tenant_id=tenant_id, actor_user_id=actor_user_id, action=action, entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None, before=before, after=after,
    ))
