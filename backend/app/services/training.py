"""Training outlets: the real counter, menu and screens, where nothing counts.

A training outlet is a copy of a real one (same brand menu, prices and taxes, its own invoice series
'TR1/2627/000001'). Bills there print "TRAINING - NOT A BILL". They never reach reports, GST, Tally,
owner alerts or SMS; they never earn or spend real customers' loyalty; UPI and card stay in test mode
(no real money); stock moves only in the training outlet's own stock. Reset starts a clean copy."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Outlet, UserOutlet

COPY = ("brand_id", "gstin", "fssai", "address", "state_code", "phone", "bill_footer", "car_number_required",
        "table_labels", "stock_check_frequency_days")


def is_training(db: Session, outlet_id: uuid.UUID | None) -> bool:
    if outlet_id is None:
        return False
    cache: dict = db.info.setdefault("wise_training", {})
    if outlet_id not in cache:
        o = db.get(Outlet, outlet_id)
        cache[outlet_id] = bool(o and o.is_training)
    return cache[outlet_id]


def real_outlet_ids(db: Session, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if not ids:
        return ids
    training = set(db.scalars(select(Outlet.id).where(Outlet.id.in_(ids), Outlet.is_training)))
    return [i for i in ids if i not in training]


def _free_code(db: Session) -> str:
    used = set(db.scalars(select(Outlet.code)))
    for n in range(1, 100):
        if f"TR{n}" not in used:
            return f"TR{n}"
    raise ValueError("Too many training outlets")


def make_training(db: Session, src: Outlet) -> Outlet:
    t = Outlet(tenant_id=src.tenant_id, name=f"Training: {src.name}"[:120], code=_free_code(db), is_training=True,
               training_of=src.id, upi_vpa=None, upi_payee_name=None, print_kots=False, qr_ordering=False,
               kiosk_enabled=False, **{k: getattr(src, k) for k in COPY})
    db.add(t)
    db.flush()
    # Everyone who works at the real outlet can practise at its training copy.
    for uid in set(db.scalars(select(UserOutlet.user_id).where(UserOutlet.outlet_id == src.id))):
        db.add(UserOutlet(user_id=uid, outlet_id=t.id, tenant_id=src.tenant_id))
    db.flush()
    return t
