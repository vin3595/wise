"""Loyalty settings (owner/manager), the customer lookup at billing, and the customer list (CRM)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.loyalty import MODES
from ..core.permissions import Perm
from ..core.contact import normalize_phone
from ..deps import Auth, get_db, require
from ..models import Bill, Customer, LoyaltyEntry, LoyaltySettings, User
from ..services import audit
from ..services import loyalty as ly
from .common import get_or_404

router = APIRouter(tags=["loyalty"])


class LoyaltyIn(BaseModel):
    mode: str
    earn_bp: int = Field(ge=0, le=5000)
    min_bill_paise: int = Field(ge=0)
    redeem_min_paise: int = Field(ge=0)
    max_redeem_bp: int = Field(ge=0, le=10000)
    expiry_days: int = Field(ge=0, le=3650)
    stamp_min_paise: int = Field(ge=0)
    stamps_needed: int = Field(ge=1, le=100)
    reward_paise: int = Field(ge=0)
    reward_label: str = Field(min_length=2, max_length=60)

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in MODES:
            raise ValueError("mode must be off, points, stamps or both")
        return v


class AdjustIn(BaseModel):
    points_paise: int = 0
    stamps: int = 0
    note: str = Field(min_length=3, max_length=120)


def _cfg(db: Session, tenant_id: uuid.UUID) -> LoyaltySettings:
    cfg = db.get(LoyaltySettings, tenant_id)
    if cfg is None:
        cfg = LoyaltySettings(tenant_id=tenant_id)
        db.add(cfg)
        db.flush()
    return cfg


def _cfg_out(cfg: LoyaltySettings) -> dict:
    return {k: getattr(cfg, k) for k in LoyaltyIn.model_fields}


@router.get("/tenant/loyalty")
def get_loyalty(auth: Auth = Depends(require(Perm.loyalty_manage)), db: Session = Depends(get_db)) -> dict:
    return _cfg_out(_cfg(db, auth.tenant_id))


@router.put("/tenant/loyalty")
def put_loyalty(body: LoyaltyIn, auth: Auth = Depends(require(Perm.loyalty_manage)), db: Session = Depends(get_db)) -> dict:
    cfg = _cfg(db, auth.tenant_id)
    before = _cfg_out(cfg)
    for k, v in body.model_dump().items():
        setattr(cfg, k, v)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "update", "loyalty_settings", None, before, _cfg_out(cfg))
    return _cfg_out(cfg)


@router.get("/loyalty/customer")
def lookup(phone: str, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    """At billing, once the mobile number is typed: what the customer has."""
    try:
        ph = normalize_phone(phone)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    r = ly.rules(db, auth.tenant_id)
    cfg = db.get(LoyaltySettings, auth.tenant_id)
    out = {"mode": r.mode, "phone": ph, "known": False, "name": None, "points_paise": 0, "stamps": 0,
           "stamps_needed": r.stamps_needed, "reward_paise": r.reward_paise, "reward_label": cfg.reward_label if cfg else "",
           "redeem_min_paise": r.redeem_min_paise, "max_redeem_bp": r.max_redeem_bp, "visits": 0}
    if r.mode == "off":
        return out
    c = ly.customer(db, auth.tenant_id, ph, create=False)
    if c is None:
        return out
    ly.expire_if_lapsed(db, r, c)
    visits = db.scalar(select(func.count()).select_from(Bill).where(Bill.customer_phone == ph, Bill.status == "paid")) or 0
    return out | {"known": True, "name": c.name, "points_paise": c.points_paise, "stamps": c.stamps, "visits": int(visits)}


@router.get("/loyalty/customers")
def customers(q: str | None = None, sort: str = "spent", auth: Auth = Depends(require(Perm.loyalty_manage)),
              db: Session = Depends(get_db)) -> list[dict]:
    agg = (select(Bill.customer_phone.label("phone"), func.count().label("visits"), func.sum(Bill.total_paise).label("spent"),
                  func.max(Bill.business_date).label("last"), func.min(Bill.business_date).label("first"))
           .where(Bill.status == "paid", Bill.customer_phone.is_not(None)).group_by(Bill.customer_phone).subquery())
    stmt = select(Customer, agg.c.visits, agg.c.spent, agg.c.last, agg.c.first).join(agg, agg.c.phone == Customer.phone, isouter=True)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Customer.phone.like(like), Customer.name.ilike(like)))
    order = {"spent": agg.c.spent.desc().nulls_last(), "visits": agg.c.visits.desc().nulls_last(), "recent": agg.c.last.desc().nulls_last(),
             "points": Customer.points_paise.desc(), "lapsing": agg.c.last.asc().nulls_last()}.get(sort, agg.c.spent.desc().nulls_last())
    out = []
    for c, visits, spent, last, first in db.execute(stmt.order_by(order).limit(500)):
        out.append({"id": str(c.id), "phone": c.phone, "name": c.name, "visits": int(visits or 0), "spent_paise": int(spent or 0),
                    "avg_paise": int(spent or 0) // int(visits) if visits else 0, "points_paise": c.points_paise, "stamps": c.stamps,
                    "first_visit": str(first) if first else None, "last_visit": str(last) if last else None})
    return out


@router.get("/loyalty/customers/{customer_id}/entries")
def entries(customer_id: uuid.UUID, auth: Auth = Depends(require(Perm.loyalty_manage)), db: Session = Depends(get_db)) -> list[dict]:
    get_or_404(db, Customer, customer_id, "Customer")
    names = {u.id: u.name for u in db.scalars(select(User))}
    bills = {b.id: b.invoice_no for b in db.scalars(select(Bill).join(LoyaltyEntry, LoyaltyEntry.bill_id == Bill.id)
                                                    .where(LoyaltyEntry.customer_id == customer_id))}
    return [{"at": e.created_at.isoformat() if e.created_at else None, "kind": e.kind, "points_paise": e.points_paise, "stamps": e.stamps,
             "bill": bills.get(e.bill_id) if e.bill_id else None, "note": e.note, "by": names.get(e.created_by) if e.created_by else None}
            for e in db.scalars(select(LoyaltyEntry).where(LoyaltyEntry.customer_id == customer_id).order_by(LoyaltyEntry.created_at.desc()).limit(200))]


@router.post("/loyalty/customers/{customer_id}/adjust")
def adjust(customer_id: uuid.UUID, body: AdjustIn, auth: Auth = Depends(require(Perm.loyalty_manage)), db: Session = Depends(get_db)) -> dict:
    c = db.scalar(select(Customer).where(Customer.id == customer_id).with_for_update())
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    if c.points_paise + body.points_paise < 0 or c.stamps + body.stamps < 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That would take the customer below zero")
    ly.post(db, c, "adjust", body.points_paise, body.stamps, None, body.note.strip(), auth.user_id)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "adjust", "loyalty", c.id, after=body.model_dump())
    return {"points_paise": c.points_paise, "stamps": c.stamps}
