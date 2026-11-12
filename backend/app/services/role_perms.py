"""Each restaurant's own "who can do what". Read on every permission check, so kept in a small cache
(a change shows within a few seconds on every worker; the worker that saved it sees it at once)."""
from __future__ import annotations

import time
import uuid
from threading import Lock

from sqlalchemy import select

from ..core.permissions import ALL, ROLE_PERMS, Perm, Role

TTL = 10.0
_cache: dict[uuid.UUID, tuple[float, dict[str, set[Perm]]]] = {}
_lock = Lock()


def _load(tenant_id: uuid.UUID) -> dict[str, set[Perm]]:
    from ..db import system_session
    from ..models import RolePermission
    out: dict[str, set[Perm]] = {}
    with system_session() as db:   # filtered by tenant explicitly; runs before the request's own session
        for r in db.scalars(select(RolePermission).where(RolePermission.tenant_id == tenant_id)):
            out[r.role] = {Perm(p) for p in r.perms if p in Perm._value2member_map_}
    return out


def overrides(tenant_id: uuid.UUID) -> dict[str, set[Perm]]:
    now = time.monotonic()
    hit = _cache.get(tenant_id)
    if hit and now - hit[0] < TTL:
        return hit[1]
    try:
        data = _load(tenant_id)
    except Exception:  # database not reachable (tests without one): defaults
        data = hit[1] if hit else {}
    with _lock:
        _cache[tenant_id] = (now, data)
    return data


def effective(tenant_id: uuid.UUID, role: Role | str) -> set[Perm]:
    r = Role(role)
    if r == Role.owner:
        return set(ALL)
    return overrides(tenant_id).get(r.value, ROLE_PERMS.get(r, set()))


def forget(tenant_id: uuid.UUID) -> None:
    with _lock:
        _cache.pop(tenant_id, None)
