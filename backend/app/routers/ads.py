"""Ads on the customer screen: pictures and videos played on the counter's second monitor while it is idle."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.permissions import Perm
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..models import AdSlide, Outlet
from ..services import audit
from ..services.uploads import remove_upload, save_ad
from .common import get_or_404

router = APIRouter(tags=["ads"])
IST = timezone(timedelta(hours=5, minutes=30))
MAX_SLIDES = 40


def _today() -> date:
    return datetime.now(IST).date()


def _out(a: AdSlide) -> dict:
    today = _today()
    live = a.is_active and (a.starts_on is None or a.starts_on <= today) and (a.ends_on is None or a.ends_on >= today)
    return {"id": str(a.id), "outlet_id": str(a.outlet_id) if a.outlet_id else None, "kind": a.kind, "url": a.url, "title": a.title,
            "seconds": a.seconds, "sort": a.sort, "starts_on": a.starts_on.isoformat() if a.starts_on else None,
            "ends_on": a.ends_on.isoformat() if a.ends_on else None, "is_active": a.is_active, "showing_now": live}


@router.get("/tenant/ads")
def list_ads(auth: Auth = Depends(require(Perm.ads_manage)), db: Session = Depends(get_db)) -> list[dict]:
    return [_out(a) for a in db.scalars(select(AdSlide).order_by(AdSlide.sort, AdSlide.created_at))]


@router.post("/tenant/ads", status_code=201)
def add_ad(file: UploadFile = File(...), title: str | None = Form(default=None), outlet_id: str | None = Form(default=None),
           seconds: int = Form(default=8), starts_on: date | None = Form(default=None), ends_on: date | None = Form(default=None),
           auth: Auth = Depends(require(Perm.ads_manage)), db: Session = Depends(get_db)) -> dict:
    if len(list(db.scalars(select(AdSlide.id)))) >= MAX_SLIDES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Up to {MAX_SLIDES} ads. Remove old ones first.")
    oid = uuid.UUID(outlet_id) if outlet_id else None
    if oid:
        get_or_404(db, Outlet, oid, "Outlet")
    if not 3 <= seconds <= 120:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Show a picture for 3 to 120 seconds")
    if starts_on and ends_on and ends_on < starts_on:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The end date is before the start date")
    kind, url = save_ad(file, auth.tenant_id)
    last = max((a.sort for a in db.scalars(select(AdSlide))), default=0)
    a = AdSlide(tenant_id=auth.tenant_id, outlet_id=oid, kind=kind, url=url, title=(title or "").strip()[:80] or None,
                seconds=seconds, sort=last + 1, starts_on=starts_on, ends_on=ends_on)
    db.add(a)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "ad", a.id, after={"title": a.title, "kind": kind, "outlet_id": outlet_id})
    return _out(a)


class AdPatch(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    outlet_id: uuid.UUID | None = None
    seconds: int = Field(default=8, ge=3, le=120)
    sort: int = 0
    starts_on: date | None = None
    ends_on: date | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _dates(self) -> "AdPatch":
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("The end date is before the start date")
        return self


@router.put("/tenant/ads/{ad_id}")
def edit_ad(ad_id: uuid.UUID, body: AdPatch, auth: Auth = Depends(require(Perm.ads_manage)), db: Session = Depends(get_db)) -> dict:
    a = get_or_404(db, AdSlide, ad_id, "Ad")
    if body.outlet_id:
        get_or_404(db, Outlet, body.outlet_id, "Outlet")
    a.title = (body.title or "").strip() or None
    a.outlet_id, a.seconds, a.sort, a.starts_on, a.ends_on, a.is_active = body.outlet_id, body.seconds, body.sort, body.starts_on, body.ends_on, body.is_active
    audit.record(db, auth.tenant_id, auth.user_id, "update", "ad", a.id, after=body.model_dump(mode="json"))
    return _out(a)


@router.delete("/tenant/ads/{ad_id}")
def delete_ad(ad_id: uuid.UUID, auth: Auth = Depends(require(Perm.ads_manage)), db: Session = Depends(get_db)) -> dict:
    a = get_or_404(db, AdSlide, ad_id, "Ad")
    url = a.url
    audit.record(db, auth.tenant_id, auth.user_id, "delete", "ad", a.id, before={"title": a.title, "url": url})
    db.delete(a)
    db.flush()
    remove_upload(url, auth.tenant_id)
    return {"ok": True}


@router.get("/outlets/{outlet_id}/ads")
def outlet_ads(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> list[dict]:
    """What this outlet's customer screen plays today, in order."""
    ensure_outlet_access(db, auth, outlet_id)
    today = _today()
    q = (select(AdSlide).where(AdSlide.is_active, or_(AdSlide.outlet_id.is_(None), AdSlide.outlet_id == outlet_id),
                               or_(AdSlide.starts_on.is_(None), AdSlide.starts_on <= today),
                               or_(AdSlide.ends_on.is_(None), AdSlide.ends_on >= today))
         .order_by(AdSlide.sort, AdSlide.created_at))
    return [{"id": str(a.id), "kind": a.kind, "url": a.url, "seconds": a.seconds} for a in db.scalars(q)]
