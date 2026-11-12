"""Helpers shared by routers."""
from __future__ import annotations

import uuid
from typing import TypeVar

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.permissions import Role
from ..core.security import create_token
from ..models import Base, Tenant, User, UserOutlet
from ..schemas import TenantOut, TokenOut, UserOut

T = TypeVar("T", bound=Base)


def get_or_404(db: Session, model: type[T], obj_id: uuid.UUID, label: str) -> T:
    obj = db.get(model, obj_id)  # RLS already hides other tenants' rows
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} not found")
    return obj


def user_out(db: Session, user: User) -> UserOut:
    outlet_ids = list(db.scalars(select(UserOutlet.outlet_id).where(UserOutlet.user_id == user.id)))
    out = UserOut.model_validate(user)
    out.has_pin = bool(user.pin_hash)
    out.outlet_ids = outlet_ids
    return out


def issue_token(db: Session, user: User, tenant: Tenant, outlet_id: uuid.UUID | None = None) -> TokenOut:
    s = get_settings()
    staff = outlet_id is not None and Role(user.role) not in (Role.owner,)
    claims = {"sub": str(user.id), "tid": str(tenant.id), "role": user.role,
              "via": "pin" if outlet_id else "otp"}
    if outlet_id:
        claims["oid"] = str(outlet_id)
    token = create_token(claims, s.jwt_secret, s.staff_token_hours if staff else s.owner_token_hours)
    return TokenOut(token=token, user=user_out(db, user), tenant=TenantOut.model_validate(tenant),
                    outlet_id=outlet_id)
