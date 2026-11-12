"""Restaurant profile, white-label branding, brands and outlets."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.permissions import Perm, Role
from ..deps import Auth, get_auth, get_db, require
from ..models import Brand, Outlet, Tenant, User, UserOutlet
from ..schemas import (
    BrandIn, BrandingUpdate, BrandOut, OutletIn, OutletOut, TenantOut, UserOut,
)
from ..services import audit
from ..services.uploads import save_image as _save_image
from .common import get_or_404, user_out

router = APIRouter(prefix="/tenant", tags=["tenant"])



@router.get("/me")
def me(auth: Auth = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    tenant = db.get(Tenant, auth.tenant_id)
    user = db.get(User, auth.user_id)
    out: UserOut = user_out(db, user)
    return {
        "tenant": TenantOut.model_validate(tenant).model_dump(mode="json"),
        "user": out.model_dump(mode="json"),
        "outlet_id": str(auth.outlet_id) if auth.outlet_id else None,
        "training": bool(auth.outlet_id and (o := db.get(Outlet, auth.outlet_id)) and o.is_training),
        "permissions": sorted(p.value for p in Perm if auth.can(p)),
    }


@router.patch("/branding", response_model=TenantOut)
def update_branding(body: BrandingUpdate, auth: Auth = Depends(require(Perm.tenant_settings)),
                    db: Session = Depends(get_db)) -> TenantOut:
    tenant = db.get(Tenant, auth.tenant_id)
    before = audit.snapshot(tenant)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(tenant, key, value)
    db.flush()
    audit.record(db, tenant.id, auth.user_id, "update", "branding", tenant.id, before, audit.snapshot(tenant))
    return TenantOut.model_validate(tenant)


@router.post("/branding/logo", response_model=TenantOut)
def upload_logo(file: UploadFile = File(...), auth: Auth = Depends(require(Perm.tenant_settings)),
                db: Session = Depends(get_db)) -> TenantOut:
    tenant = db.get(Tenant, auth.tenant_id)
    before = tenant.logo_url
    tenant.logo_url = _save_image(file, tenant.id, "logo")
    audit.record(db, tenant.id, auth.user_id, "update", "logo", tenant.id, {"logo_url": before},
                 {"logo_url": tenant.logo_url})
    return TenantOut.model_validate(tenant)


# ------------------------------------------------------------------ brands

@router.get("/brands", response_model=list[BrandOut])
def list_brands(auth: Auth = Depends(get_auth), db: Session = Depends(get_db)) -> list[Brand]:
    return list(db.scalars(select(Brand).order_by(Brand.name)))


@router.post("/brands", response_model=BrandOut, status_code=201)
def create_brand(body: BrandIn, auth: Auth = Depends(require(Perm.tenant_settings)),
                 db: Session = Depends(get_db)) -> Brand:
    brand = Brand(tenant_id=auth.tenant_id, **body.model_dump())
    db.add(brand)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "brand", brand.id, after=audit.snapshot(brand))
    return brand


@router.patch("/brands/{brand_id}", response_model=BrandOut)
def update_brand(brand_id: uuid.UUID, body: BrandIn, auth: Auth = Depends(require(Perm.tenant_settings)),
                 db: Session = Depends(get_db)) -> Brand:
    brand = get_or_404(db, Brand, brand_id, "Brand")
    before = audit.snapshot(brand)
    for key, value in body.model_dump().items():
        setattr(brand, key, value)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "update", "brand", brand.id, before, audit.snapshot(brand))
    return brand


@router.post("/brands/{brand_id}/logo", response_model=BrandOut)
def upload_brand_logo(brand_id: uuid.UUID, file: UploadFile = File(...),
                      auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)) -> Brand:
    brand = get_or_404(db, Brand, brand_id, "Brand")
    brand.logo_url = _save_image(file, auth.tenant_id, f"brand-{brand.id.hex[:8]}")
    audit.record(db, auth.tenant_id, auth.user_id, "update", "brand_logo", brand.id, after={"logo_url": brand.logo_url})
    return brand


# ------------------------------------------------------------------ outlets

@router.get("/outlets", response_model=list[OutletOut])
def list_outlets(auth: Auth = Depends(get_auth), db: Session = Depends(get_db)) -> list[Outlet]:
    q = select(Outlet).order_by(Outlet.name)
    if auth.role != Role.owner:
        q = q.join(UserOutlet, UserOutlet.outlet_id == Outlet.id).where(UserOutlet.user_id == auth.user_id)
    return list(db.scalars(q))


@router.post("/outlets", response_model=OutletOut, status_code=201)
def create_outlet(body: OutletIn, auth: Auth = Depends(require(Perm.outlets_manage)),
                  db: Session = Depends(get_db)) -> Outlet:
    get_or_404(db, Brand, body.brand_id, "Brand")
    outlet = Outlet(tenant_id=auth.tenant_id, **body.model_dump())
    if outlet.print_kots:
        outlet.print_since = datetime.now(timezone.utc)
    db.add(outlet)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "outlet", outlet.id, after=audit.snapshot(outlet))
    return outlet


@router.patch("/outlets/{outlet_id}", response_model=OutletOut)
def update_outlet(outlet_id: uuid.UUID, body: OutletIn, auth: Auth = Depends(require(Perm.outlets_manage)),
                  db: Session = Depends(get_db)) -> Outlet:
    outlet = get_or_404(db, Outlet, outlet_id, "Outlet")
    get_or_404(db, Brand, body.brand_id, "Brand")
    before = audit.snapshot(outlet)
    if body.print_kots and not outlet.print_kots:
        # Turning printing on prints new KOTs only, not the whole day's backlog.
        outlet.print_since = datetime.now(timezone.utc)
    for key, value in body.model_dump().items():
        setattr(outlet, key, value)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "update", "outlet", outlet.id, before, audit.snapshot(outlet))
    return outlet


# ------------------------------------------------------------------ training outlets

@router.post("/outlets/{outlet_id}/training", response_model=OutletOut)
def training_outlet(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.outlets_manage)),
                    db: Session = Depends(get_db)) -> Outlet:
    """The practice copy of this outlet (made the first time). Staff pick it at sign-in."""
    from ..services.training import make_training
    src = get_or_404(db, Outlet, outlet_id, "Outlet")
    if src.is_training:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This is already a training outlet")
    t = db.scalar(select(Outlet).where(Outlet.training_of == src.id, Outlet.is_training, Outlet.is_active))
    if t is None:
        t = make_training(db, src)
        audit.record(db, auth.tenant_id, auth.user_id, "create_training", "outlet", t.id, after={"of": src.name, "code": t.code})
    return t


@router.post("/outlets/{outlet_id}/training/reset", response_model=OutletOut)
def reset_training(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.outlets_manage)),
                   db: Session = Depends(get_db)) -> Outlet:
    """Start practice from clean: the old training copy is switched off (its practice stays out of every
    report) and a fresh one replaces it."""
    from ..services.training import make_training
    old = get_or_404(db, Outlet, outlet_id, "Outlet")
    if not old.is_training or old.training_of is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only a training outlet can be reset")
    src = get_or_404(db, Outlet, old.training_of, "Outlet")
    old.is_active = False
    old.name = (old.name + " (old)")[:120]
    db.flush()
    t = make_training(db, src)
    audit.record(db, auth.tenant_id, auth.user_id, "reset_training", "outlet", t.id, after={"of": src.name, "old": str(old.id)})
    return t
