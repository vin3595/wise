"""Signup (new restaurant), owner/manager OTP login, staff PIN login."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..config import get_settings
from ..core.contact import mask_identifier, normalize_identifier
from ..core.login_id import normalize_login_id, validate_login_id
from ..core.permissions import OTP_ROLES, Role
from ..core.security import pin_lookup_key, verify_secret
from ..db import system_session
from ..models import Brand, Outlet, TaxGroup, Tenant, User, UserOutlet
from ..schemas import OtpRequest, OtpSent, OtpVerify, PinLogin, SignupStart, SignupVerify, TokenOut
from ..services import audit, otp, throttle
from .common import issue_token

router = APIRouter(prefix="/auth", tags=["auth"])


def _dev_code(code: str) -> str | None:
    # Only in local development. The Wise Box uses provider "box": the code goes to the box's log
    # (sudo wise login-code) and is never sent back to the browser.
    return code if get_settings().otp_provider == "console" else None


def _check_signup_open(db) -> None:
    """signup_mode: open (hosted SaaS) | first (Wise Box: only the first restaurant) | closed."""
    mode = get_settings().signup_mode
    if mode == "closed" or (mode == "first" and db.scalar(select(Tenant.id).limit(1)) is not None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sign-up is closed on this Wise Box")


@router.get("/login-id-available")
def login_id_available(login_id: str) -> dict:
    try:
        value = validate_login_id(login_id)
    except ValueError as e:
        return {"available": False, "reason": str(e)}
    with system_session() as db:
        taken = db.scalar(select(Tenant.id).where(Tenant.login_id == value)) is not None
    return {"available": not taken, "reason": "That login ID is taken" if taken else None}


@router.post("/signup/start", response_model=OtpSent)
def signup_start(body: SignupStart) -> OtpSent:
    error = None
    with system_session() as db:
        _check_signup_open(db)
        if db.scalar(select(Tenant.id).where(Tenant.login_id == body.login_id)) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "That login ID is taken")
        try:
            code = otp.issue(db, body.phone, "signup")
        except otp.OtpError as e:
            error = e
    if error:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(error))
    return OtpSent(sent_to=mask_identifier("phone", body.phone), dev_otp=_dev_code(code))


@router.post("/signup/verify", response_model=TokenOut)
def signup_verify(body: SignupVerify) -> TokenOut:
    s = get_settings()
    error = None
    result = None
    with system_session() as db:
        _check_signup_open(db)
        try:
            otp.verify(db, body.phone, "signup", body.otp)
        except otp.OtpError as e:
            error = e  # keep the session committing so the failed attempt is counted
        if error is None:
            if db.scalar(select(Tenant.id).where(Tenant.login_id == body.login_id)) is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, "That login ID was just taken")
            tenant = Tenant(
                login_id=body.login_id, name=body.restaurant_name, owner_email=body.email,
                owner_phone=body.phone, app_name=body.restaurant_name,
                trial_ends_at=datetime.now(timezone.utc) + timedelta(days=s.trial_days),
            )
            db.add(tenant)
            db.flush()
            db.add(Brand(tenant_id=tenant.id, name=body.restaurant_name))
            db.add(TaxGroup(tenant_id=tenant.id, name="GST 5%", cgst_bp=250, sgst_bp=250, is_default=True))
            owner = User(tenant_id=tenant.id, name=body.owner_name, email=body.email, phone=body.phone,
                         role=Role.owner.value, last_login_at=datetime.now(timezone.utc))
            db.add(owner)
            db.flush()
            audit.record(db, tenant.id, owner.id, "signup", "tenant", tenant.id, after=audit.snapshot(tenant))
            result = issue_token(db, owner, tenant)
    if error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error))
    assert result is not None
    return result


def _find_tenant(db, login_id: str) -> Tenant | None:
    return db.scalar(select(Tenant).where(Tenant.login_id == normalize_login_id(login_id)))


@router.post("/otp/request", response_model=OtpSent)
def otp_request(body: OtpRequest) -> OtpSent:
    try:
        kind, ident = normalize_identifier(body.identifier)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    error = None
    code = None
    with system_session() as db:
        tenant = _find_tenant(db, body.login_id)
        user = None
        if tenant is not None:
            col = User.email if kind == "email" else User.phone
            user = db.scalar(select(User).where(User.tenant_id == tenant.id, col == ident, User.is_active))
        # Same response whether or not the account exists, so login IDs/numbers can't be probed.
        if tenant is not None and user is not None and Role(user.role) in OTP_ROLES \
                and tenant.status != "suspended":
            try:
                code = otp.issue(db, f"{tenant.id}:{ident}", "login")
            except otp.OtpError as e:
                error = e
    if error:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(error))
    return OtpSent(sent_to=mask_identifier(kind, ident), dev_otp=_dev_code(code) if code else None)


@router.post("/otp/verify", response_model=TokenOut)
def otp_verify(body: OtpVerify) -> TokenOut:
    try:
        kind, ident = normalize_identifier(body.identifier)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    error = None
    result = None
    with system_session() as db:
        tenant = _find_tenant(db, body.login_id)
        if tenant is None:
            error = otp.OtpError("Wrong code")
        else:
            try:
                otp.verify(db, f"{tenant.id}:{ident}", "login", body.otp)
            except otp.OtpError as e:
                error = e
        if error is None and tenant is not None:
            col = User.email if kind == "email" else User.phone
            user = db.scalar(select(User).where(User.tenant_id == tenant.id, col == ident, User.is_active))
            if user is None or Role(user.role) not in OTP_ROLES:
                error = otp.OtpError("Account not found")
            else:
                user.last_login_at = datetime.now(timezone.utc)
                audit.record(db, tenant.id, user.id, "login_otp", "user", user.id)
                result = issue_token(db, user, tenant)
    if error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error))
    assert result is not None
    return result


@router.post("/pin", response_model=TokenOut)
def pin_login(body: PinLogin) -> TokenOut:
    s = get_settings()
    error: tuple[int, str] | None = None
    result = None
    with system_session() as db:
        tenant = _find_tenant(db, body.login_id)
        outlet = db.get(Outlet, body.outlet_id) if tenant else None
        if tenant is None or outlet is None or outlet.tenant_id != tenant.id or not outlet.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Outlet not found")
        key = f"pin:{tenant.id}:{outlet.id}"
        mins = throttle.locked_minutes_left(db, key)
        if mins:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                f"Too many wrong PINs. Try again in {mins} min.")
        user = db.scalar(select(User).where(
            User.tenant_id == tenant.id,
            User.pin_lookup == pin_lookup_key(body.pin, str(tenant.id), s.jwt_secret),
            User.is_active,
        ))
        assigned = user is not None and (
            user.role == Role.owner.value
            or db.get(UserOutlet, {"user_id": user.id, "outlet_id": outlet.id}) is not None
        )
        if not assigned or not verify_secret(body.pin, user.pin_hash):
            throttle.record_failure(db, key)
            error = (status.HTTP_401_UNAUTHORIZED, "Wrong PIN")
        elif tenant.status == "suspended":
            error = (status.HTTP_403_FORBIDDEN, "This restaurant's account is suspended")
        else:
            throttle.reset(db, key)
            user.last_login_at = datetime.now(timezone.utc)
            audit.record(db, tenant.id, user.id, "login_pin", "user", user.id, after={"outlet_id": str(outlet.id)})
            result = issue_token(db, user, tenant, outlet_id=outlet.id)
    if error:
        raise HTTPException(*error)
    assert result is not None
    return result


class ScreenLogin(BaseModel):
    key: str = Field(min_length=10, max_length=40)


@router.post("/kitchen-screen", response_model=TokenOut)
def kitchen_screen_login(body: ScreenLogin) -> TokenOut:
    """The kitchen display signs in with its outlet's kitchen link: no PIN, no daily login.
    It acts as the outlet's 'Kitchen screen' (kitchen role permissions, this outlet only)."""
    with system_session() as db:
        outlet = db.scalar(select(Outlet).where(Outlet.kitchen_key == body.key, Outlet.is_active))
        tenant = db.get(Tenant, outlet.tenant_id) if outlet else None
        if outlet is None or tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This kitchen link isn't valid. Ask the manager for the outlet's kitchen link.")
        if tenant.status == "suspended":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This restaurant's account is suspended")
        user = db.scalar(select(User).join(UserOutlet, UserOutlet.user_id == User.id)
                         .where(User.tenant_id == tenant.id, User.is_screen, User.is_active, UserOutlet.outlet_id == outlet.id))
        if user is None:
            user = User(tenant_id=tenant.id, name=f"Kitchen screen ({outlet.name})"[:120], role=Role.kitchen.value, is_screen=True)
            db.add(user)
            db.flush()
            db.add(UserOutlet(user_id=user.id, outlet_id=outlet.id, tenant_id=tenant.id))
            db.flush()
        user.last_login_at = datetime.now(timezone.utc)
        return issue_token(db, user, tenant, outlet_id=outlet.id)


@router.get("/outlets")
def outlets_for_device(login_id: str) -> list[dict]:
    """Outlet list for setting up a counter device. Names only; no sensitive data."""
    with system_session() as db:
        tenant = _find_tenant(db, login_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Restaurant not found")
        rows = db.execute(
            select(Outlet.id, Outlet.name).where(Outlet.tenant_id == tenant.id, Outlet.is_active)
            .order_by(Outlet.name)
        ).all()
    return [{"id": str(r.id), "name": r.name} for r in rows]

