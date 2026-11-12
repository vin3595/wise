"""Who can do what: the owner chooses each role's permissions. The owner always has everything."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.permissions import ALL, CATALOG, EDITABLE_ROLES, ROLE_PERMS, Perm, Role
from ..deps import Auth, get_db, require
from ..models import RolePermission
from ..services import audit, role_perms

router = APIRouter(prefix="/tenant/roles", tags=["roles"])
ROLE_LABEL = {"owner": "Owner", "manager": "Manager", "cashier": "Cashier", "captain": "Captain", "kitchen": "Kitchen"}


class RolePermsIn(BaseModel):
    perms: list[str]


def _owner(auth: Auth) -> None:
    if auth.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can change who can do what")


def _view(db: Session, auth: Auth) -> dict:
    rows = {r.role: r for r in db.scalars(select(RolePermission))}
    roles = [{"role": "owner", "label": "Owner", "perms": sorted(p.value for p in ALL), "default": sorted(p.value for p in ALL),
              "editable": False, "changed": False}]
    for r in EDITABLE_ROLES:
        default = sorted(p.value for p in ROLE_PERMS[r])
        cur = sorted(rows[r.value].perms) if r.value in rows else default
        roles.append({"role": r.value, "label": ROLE_LABEL[r.value], "perms": cur, "default": default, "editable": True,
                      "changed": cur != default})
    return {"roles": roles, "permissions": [{"key": p.value, "group": g, "label": lbl, "help": h, "sensitive": sens}
                                            for p, (g, lbl, h, sens) in CATALOG.items()], "can_edit": auth.role == Role.owner}


@router.get("")
def get_roles(auth: Auth = Depends(require(Perm.users_manage)), db: Session = Depends(get_db)) -> dict:
    return _view(db, auth)


@router.put("/{role}")
def put_role(role: str, body: RolePermsIn, auth: Auth = Depends(require(Perm.users_manage)), db: Session = Depends(get_db)) -> dict:
    _owner(auth)
    if role not in {r.value for r in EDITABLE_ROLES}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The owner always has everything")
    bad = [p for p in body.perms if p not in Perm._value2member_map_]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown permission: {bad[0]}")
    perms = sorted(set(body.perms))
    row = db.get(RolePermission, {"tenant_id": auth.tenant_id, "role": role})
    before = sorted(row.perms) if row else sorted(p.value for p in ROLE_PERMS[Role(role)])
    if perms == sorted(p.value for p in ROLE_PERMS[Role(role)]):
        if row:
            db.delete(row)            # back to the defaults
    elif row:
        row.perms, row.updated_by = perms, auth.user_id
    else:
        db.add(RolePermission(tenant_id=auth.tenant_id, role=role, perms=perms, updated_by=auth.user_id))
    db.flush()
    role_perms.forget(auth.tenant_id)
    audit.record(db, auth.tenant_id, auth.user_id, "update", "role_permissions", None, {"role": role, "perms": before},
                 {"role": role, "perms": perms})
    return _view(db, auth)
