"""Request context: who is calling, which tenant, and a tenant-scoped DB session."""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import get_settings
from .core.permissions import Perm, Role
from .core.security import decode_token
from .db import tenant_session
from .models import Tenant, User


@dataclass(frozen=True)
class Auth:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: Role
    outlet_id: uuid.UUID | None  # set for PIN sessions on an outlet device
    via: str                     # "otp" | "pin"

    def can(self, perm: Perm) -> bool:
        from .services.role_perms import effective
        return perm in effective(self.tenant_id, self.role)


def get_auth(authorization: str | None = Header(default=None)) -> Auth:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    try:
        claims = decode_token(authorization.split(" ", 1)[1], get_settings().jwt_secret)
        return Auth(
            user_id=uuid.UUID(claims["sub"]),
            tenant_id=uuid.UUID(claims["tid"]),
            role=Role(claims["role"]),
            outlet_id=uuid.UUID(claims["oid"]) if claims.get("oid") else None,
            via=claims.get("via", "otp"),
        )
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, sign in again") from None


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_db(request: Request, auth: Auth = Depends(get_auth)) -> Iterator[Session]:
    """Tenant-scoped session. Also re-checks the user and tenant on every request so a
    deactivated user or suspended restaurant is locked out immediately."""
    with tenant_session(auth.tenant_id) as db:
        user = db.get(User, auth.user_id)
        if user is None or not user.is_active or user.tenant_id != auth.tenant_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled")
        if user.role != auth.role.value:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your role changed, sign in again")
        tenant = db.get(Tenant, auth.tenant_id)
        if tenant is None or tenant.status == "suspended":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This restaurant's account is suspended")
        if tenant.status == "read_only" and request.method in WRITE_METHODS:
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Subscription expired: the app is read-only")
        yield db


def require(perm: Perm):
    def checker(auth: Auth = Depends(get_auth)) -> Auth:
        if not auth.can(perm):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission for this")
        return auth
    return checker


def ensure_outlet_access(db: Session, auth: Auth, outlet_id: uuid.UUID) -> None:
    """Owners see all outlets; everyone else only outlets they're assigned to."""
    if auth.role == Role.owner:
        return
    if auth.outlet_id is not None and auth.outlet_id != outlet_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This device is signed in to a different outlet")
    from .models import UserOutlet
    if db.get(UserOutlet, {"user_id": auth.user_id, "outlet_id": outlet_id}) is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this outlet")
