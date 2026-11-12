"""Staff and managers: create, edit, assign outlets, set PINs."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.permissions import OTP_ROLES, Perm, Role, can_assign
from ..core.security import hash_secret, pin_lookup_key
from ..deps import Auth, ensure_outlet_access, require
from ..deps import get_db
from ..models import Outlet, User, UserOutlet
from ..schemas import PinSet, UserIn, UserOut, UserUpdate
from ..services import audit
from .common import get_or_404, user_out

router = APIRouter(prefix="/users", tags=["users"])


def _check_assignable(auth: Auth, role: Role | str) -> None:
    if not can_assign(auth.role, role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"You can't manage {Role(role).value} accounts")


def _set_outlets(db: Session, auth: Auth, user: User, outlet_ids: list[uuid.UUID]) -> None:
    for oid in outlet_ids:
        get_or_404(db, Outlet, oid, "Outlet")
        ensure_outlet_access(db, auth, oid)
    db.execute(delete(UserOutlet).where(UserOutlet.user_id == user.id))
    for oid in dict.fromkeys(outlet_ids):
        db.add(UserOutlet(user_id=user.id, outlet_id=oid, tenant_id=user.tenant_id))


def _set_pin(db: Session, user: User, pin: str) -> None:
    lookup = pin_lookup_key(pin, str(user.tenant_id), get_settings().jwt_secret)
    clash = db.scalar(select(User.id).where(User.pin_lookup == lookup, User.id != user.id))
    if clash is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Another staff member already uses this PIN. Pick a different one.")
    user.pin_lookup = lookup
    user.pin_hash = hash_secret(pin)


def _visible_to(db: Session, auth: Auth, user: User) -> bool:
    """Managers only manage people in their own outlets."""
    if auth.role == Role.owner:
        return True
    mine = set(db.scalars(select(UserOutlet.outlet_id).where(UserOutlet.user_id == auth.user_id)))
    theirs = set(db.scalars(select(UserOutlet.outlet_id).where(UserOutlet.user_id == user.id)))
    return bool(mine & theirs) or not theirs


@router.get("", response_model=list[UserOut])
def list_users(auth: Auth = Depends(require(Perm.users_manage)), db: Session = Depends(get_db)) -> list[UserOut]:
    users = list(db.scalars(select(User).where(User.is_screen.is_(False)).order_by(User.role, User.name)))
    return [user_out(db, u) for u in users if _visible_to(db, auth, u)]


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserIn, auth: Auth = Depends(require(Perm.users_manage)),
                db: Session = Depends(get_db)) -> UserOut:
    _check_assignable(auth, body.role)
    if body.role in OTP_ROLES and not (body.email or body.phone):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Owners and managers need an email or mobile to sign in")
    if body.role not in OTP_ROLES and not body.pin:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Set a 4-digit PIN for staff")
    if body.role != Role.owner and not body.outlet_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Assign at least one outlet")
    user = User(tenant_id=auth.tenant_id, name=body.name, role=body.role.value, email=body.email,
                phone=body.phone, is_active=body.is_active)
    db.add(user)
    db.flush()
    if body.pin:
        _set_pin(db, user, body.pin)
    _set_outlets(db, auth, user, body.outlet_ids)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "user", user.id, after=audit.snapshot(user))
    return user_out(db, user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, auth: Auth = Depends(require(Perm.users_manage)),
                db: Session = Depends(get_db)) -> UserOut:
    user = get_or_404(db, User, user_id, "User")
    if not _visible_to(db, auth, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    _check_assignable(auth, user.role)
    data = body.model_dump(exclude_unset=True)
    if "role" in data:
        _check_assignable(auth, data["role"])
        data["role"] = Role(data["role"]).value
    if user.role == Role.owner.value and (data.get("is_active") is False or data.get("role", "owner") != "owner"):
        owners = db.scalar(select(func.count()).select_from(User).where(User.role == "owner", User.is_active))
        if owners <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "A restaurant needs at least one active owner")
    if user.id == auth.user_id and data.get("is_active") is False:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't deactivate yourself")
    before = audit.snapshot(user)
    outlet_ids = data.pop("outlet_ids", None)
    for key, value in data.items():
        setattr(user, key, value)
    if outlet_ids is not None:
        _set_outlets(db, auth, user, outlet_ids)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "update", "user", user.id, before, audit.snapshot(user))
    return user_out(db, user)


@router.put("/{user_id}/pin", response_model=UserOut)
def set_pin(user_id: uuid.UUID, body: PinSet, auth: Auth = Depends(require(Perm.users_manage)),
            db: Session = Depends(get_db)) -> UserOut:
    user = get_or_404(db, User, user_id, "User")
    if not _visible_to(db, auth, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id != auth.user_id:
        _check_assignable(auth, user.role)
    _set_pin(db, user, body.pin)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "set_pin", "user", user.id)
    return user_out(db, user)
