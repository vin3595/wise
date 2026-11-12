"""Manager PIN override: a cashier hits a restricted action, a manager types their PIN on the same
device, the action goes through and both names are recorded."""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.permissions import Perm, Role
from .role_perms import effective
from ..core.security import pin_lookup_key, verify_secret
from ..db import system_session
from ..models import User, UserOutlet
from . import throttle

APPROVER_ROLES = {Role.owner.value, Role.manager.value}


def verify_manager_pin(db: Session, tenant_id: uuid.UUID, outlet_id: uuid.UUID, pin: str | None) -> User:
    if not pin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Manager PIN required")
    key = f"approve:{tenant_id}:{outlet_id}"
    mins = throttle.locked_minutes_left(db, key)
    if mins:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Too many wrong manager PINs. Try again in {mins} min.")
    user = db.scalar(select(User).where(
        User.pin_lookup == pin_lookup_key(pin, str(tenant_id), get_settings().jwt_secret), User.is_active))
    ok = (
        user is not None
        and Perm.approve in effective(tenant_id, user.role)
        and verify_secret(pin, user.pin_hash)
        and (user.role == Role.owner.value
             or db.get(UserOutlet, {"user_id": user.id, "outlet_id": outlet_id}) is not None)
    )
    if not ok:
        # The request's own transaction rolls back when we raise, so count the failure in a
        # separate transaction or the lock-out would never trigger.
        with system_session() as side:
            throttle.record_failure(side, key)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Wrong manager PIN")
    throttle.reset(db, key)
    assert user is not None
    return user
