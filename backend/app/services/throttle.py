"""Lock out repeated failed PIN attempts per outlet device."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AuthThrottle


def _now() -> datetime:
    return datetime.now(timezone.utc)


def locked_minutes_left(db: Session, key: str) -> int:
    row = db.get(AuthThrottle, key)
    if row and row.locked_until and row.locked_until > _now():
        return max(1, int((row.locked_until - _now()).total_seconds() // 60) + 1)
    return 0


def record_failure(db: Session, key: str) -> None:
    s = get_settings()
    row = db.get(AuthThrottle, key, with_for_update=True)
    if row is None:
        row = AuthThrottle(key=key, failures=0)
        db.add(row)
    if row.locked_until and row.locked_until <= _now():
        row.failures = 0
        row.locked_until = None
    row.failures += 1
    row.updated_at = _now()
    if row.failures >= s.pin_max_failures:
        row.locked_until = _now() + timedelta(minutes=s.pin_lock_minutes)


def reset(db: Session, key: str) -> None:
    row = db.get(AuthThrottle, key)
    if row is not None:
        db.delete(row)
