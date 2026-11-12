"""Customers, points and stamps. Like stock, `resync_bill` posts only the difference between what a bill
should have earned/used and what was already posted for it, so it is right after billing, a correction
(different total or a different customer number) and a cancellation (everything for the bill is undone)."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.kds import business_date
from ..core.loyalty import Rules, earned, lapsed, stamp
from ..models import Bill, Customer, LoyaltyEntry, LoyaltySettings, Payment


def rules(db: Session, tenant_id: uuid.UUID) -> Rules:
    cfg = db.get(LoyaltySettings, tenant_id)
    if cfg is None:
        return Rules()
    return Rules(mode=cfg.mode, earn_bp=cfg.earn_bp, min_bill_paise=cfg.min_bill_paise, redeem_min_paise=cfg.redeem_min_paise,
                 max_redeem_bp=cfg.max_redeem_bp, expiry_days=cfg.expiry_days, stamp_min_paise=cfg.stamp_min_paise,
                 stamps_needed=cfg.stamps_needed, reward_paise=cfg.reward_paise)


def customer(db: Session, tenant_id: uuid.UUID, phone: str, name: str | None = None, create: bool = True, lock: bool = True) -> Customer | None:
    q = select(Customer).where(Customer.phone == phone)
    c = db.scalar(q.with_for_update() if lock else q)
    if c is None and create:
        c = Customer(tenant_id=tenant_id, phone=phone, name=name, points_paise=0, stamps=0)
        db.add(c)
        db.flush()
    elif c is not None and name and not c.name:
        c.name = name
    return c


def post(db: Session, c: Customer, kind: str, points: int = 0, stamps: int = 0, bill_id: uuid.UUID | None = None,
         note: str | None = None, user_id: uuid.UUID | None = None) -> None:
    if not points and not stamps:
        return
    db.add(LoyaltyEntry(tenant_id=c.tenant_id, customer_id=c.id, bill_id=bill_id, kind=kind, points_paise=points, stamps=stamps,
                        note=note, created_by=user_id))
    c.points_paise += points
    c.stamps += stamps


def expire_if_lapsed(db: Session, r: Rules, c: Customer, today: date | None = None) -> None:
    if c.points_paise > 0 and lapsed(r, c.last_visit, today or business_date()):
        post(db, c, "expire", points=-c.points_paise, note=f"No visit for over {r.expiry_days} days")


def paid_amount(db: Session, bill: Bill) -> int:
    comp = db.scalar(select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
        Payment.bill_id == bill.id, Payment.mode == "complimentary")) or 0
    return bill.total_paise - int(comp)


def resync_bill(db: Session, bill: Bill, user_id: uuid.UUID | None = None) -> None:
    from .training import is_training
    if is_training(db, bill.outlet_id):
        return                          # practice bills never touch real customers' points
    r = rules(db, bill.tenant_id)
    db.flush()
    posted: dict[uuid.UUID, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for cid, kind, p, s in db.execute(select(LoyaltyEntry.customer_id, LoyaltyEntry.kind, func.sum(LoyaltyEntry.points_paise),
                                             func.sum(LoyaltyEntry.stamps)).where(LoyaltyEntry.bill_id == bill.id)
                                      .group_by(LoyaltyEntry.customer_id, LoyaltyEntry.kind)):
        posted[cid][kind] = [int(p), int(s)]
    if r.mode == "off" and bill.status == "paid" and not posted and not bill.loyalty_points_paise and not bill.loyalty_reward_paise:
        return
    want: dict[uuid.UUID, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    if bill.status == "paid" and bill.customer_phone:
        c = customer(db, bill.tenant_id, bill.customer_phone, bill.customer_name)
        assert c is not None
        paid = paid_amount(db, bill)
        prev = posted.get(c.id, {})
        # With loyalty switched off, a correction keeps what the bill already earned.
        want[c.id]["earn"] = [earned(r, paid), stamp(r, paid)] if r.mode != "off" else list(prev.get("earn", [0, 0]))
        want[c.id]["redeem"] = [-bill.loyalty_points_paise, 0]
        if bill.loyalty_reward_paise:   # the reward uses up the stamps it needed when it was given
            want[c.id]["reward"] = list(prev["reward"]) if prev.get("reward", [0, 0])[1] else [0, -r.stamps_needed]
        if not prev:
            expire_if_lapsed(db, r, c, bill.business_date)
            c.first_visit = c.first_visit or bill.business_date
            c.last_visit = max(c.last_visit or bill.business_date, bill.business_date)
    for cid in set(want) | set(posted):
        cust = db.get(Customer, cid)
        if cust is None:
            continue
        for kind in ("earn", "redeem", "reward"):
            w, p = want.get(cid, {}).get(kind, [0, 0]), posted.get(cid, {}).get(kind, [0, 0])
            dp, ds = w[0] - p[0], w[1] - p[1]
            if dp or ds:
                note = None if not p[0] and not p[1] else ("bill cancelled" if bill.status != "paid" else "bill corrected")
                post(db, cust, kind, dp, ds, bill.id, note or bill.invoice_no, user_id)
    db.flush()
