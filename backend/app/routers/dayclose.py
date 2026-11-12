"""Day-end close: a checklist, the day's figures (Z report), and the close itself.

Before closing, the manager sees what is still open: cashiers' shifts, orders sent to the kitchen but not
billed, QR orders waiting, and whether stock was counted. Shifts and unbilled orders block the close
unless the manager closes anyway with a note. Once closed, no new bills for that day; reopening is
allowed (it is logged) for a bill that was missed.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.kds import business_date
from ..core.permissions import Perm
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..models import (
    Bill, BillLine, BillRevision, CashAlert, CashMovement, DayClose, Order, Outlet, Payment, Purchase, Shift, StockCount,
    StockMove, User,
)
from ..services import audit, sms
from ..services.billing import shift_out
from .common import get_or_404

router = APIRouter(prefix="/day-close", tags=["day close"])


class CloseIn(BaseModel):
    outlet_id: uuid.UUID
    day: date
    note: str | None = Field(default=None, max_length=300)
    close_anyway: bool = False


class ReopenIn(BaseModel):
    outlet_id: uuid.UUID
    day: date
    reason: str = Field(min_length=3, max_length=200)


def is_closed(db: Session, outlet_id: uuid.UUID, day: date) -> bool:
    return db.scalar(select(DayClose.id).where(DayClose.outlet_id == outlet_id, DayClose.business_date == day,
                                               DayClose.status == "closed")) is not None


def checks(db: Session, outlet_id: uuid.UUID, day: date) -> list[dict]:
    """What's still open. 'block' items stop the close unless the manager closes anyway."""
    out = []
    names = {u.id: u.name for u in db.scalars(select(User))}
    open_shifts = list(db.scalars(select(Shift).where(Shift.outlet_id == outlet_id, Shift.status == "open")))
    if open_shifts:
        out.append({"key": "shifts", "block": True, "text": "Shifts still open: " + ", ".join(names.get(s.opened_by, "?") for s in open_shifts),
                    "fix": "Each cashier closes their shift by counting the drawer."})
    billed = select(Bill.order_id).where(Bill.status == "paid")
    unbilled = db.scalar(select(func.count()).select_from(Order).where(
        Order.outlet_id == outlet_id, Order.business_date == day, Order.status.in_(["new", "preparing", "ready", "completed"]),
        Order.merged_into_id.is_(None), Order.id.not_in(billed), Order.token_no.is_not(None))) or 0
    if unbilled:
        out.append({"key": "orders", "block": True, "text": f"{unbilled} order{'s' if unbilled != 1 else ''} sent to the kitchen but not billed",
                    "fix": "Bill them under Orders > Open orders, or cancel them with a reason."})
    pending = db.scalar(select(func.count()).select_from(Order).where(
        Order.outlet_id == outlet_id, Order.business_date == day, Order.status == "pending")) or 0
    if pending:
        out.append({"key": "qr", "block": False, "text": f"{pending} QR / kiosk order{'s' if pending != 1 else ''} never accepted",
                    "fix": "Accept or decline them under Orders > Waiting for you."})
    if not db.scalar(select(StockCount.id).where(StockCount.outlet_id == outlet_id, StockCount.business_date == day).limit(1)):
        if db.scalar(select(StockMove.id).where(StockMove.outlet_id == outlet_id).limit(1)):
            out.append({"key": "stock", "block": False, "text": "No closing stock count today",
                        "fix": "Count the main items under Stock > Closing count."})
    alerts = db.scalar(select(func.count()).select_from(CashAlert).where(CashAlert.outlet_id == outlet_id, CashAlert.checked_at.is_(None))) or 0
    if alerts:
        out.append({"key": "cash", "block": False, "text": f"{alerts} cash difference{'s' if alerts != 1 else ''} not checked yet",
                    "fix": "Look at Cash to check on Home."})
    return out


def summary(db: Session, outlet_id: uuid.UUID, day: date) -> dict:
    paid = [Bill.outlet_id == outlet_id, Bill.business_date == day, Bill.status == "paid"]
    n, gross, disc, taxable, gst, ro, total = db.execute(select(
        func.count(), func.coalesce(func.sum(Bill.gross_paise), 0), func.coalesce(func.sum(Bill.discount_paise), 0),
        func.coalesce(func.sum(Bill.taxable_paise), 0), func.coalesce(func.sum(Bill.cgst_paise + Bill.sgst_paise + Bill.igst_paise), 0),
        func.coalesce(func.sum(Bill.round_off_paise), 0), func.coalesce(func.sum(Bill.total_paise), 0)).where(*paid)).one()
    modes = {m: int(v) for m, v in db.execute(select(Payment.mode, func.sum(Payment.amount_paise)).join(Bill, Bill.id == Payment.bill_id)
                                               .where(*paid).group_by(Payment.mode))}
    c_n, c_v = db.execute(select(func.count(), func.coalesce(func.sum(Bill.total_paise), 0)).where(
        Bill.outlet_id == outlet_id, Bill.business_date == day, Bill.status == "cancelled")).one()
    corrections = db.scalar(select(func.count()).select_from(BillRevision).join(Bill, Bill.id == BillRevision.bill_id).where(
        Bill.outlet_id == outlet_id, func.date(func.timezone("Asia/Kolkata", BillRevision.created_at)) == day)) or 0
    first, last = db.execute(select(func.min(Bill.invoice_no), func.max(Bill.invoice_no)).where(
        Bill.outlet_id == outlet_id, Bill.business_date == day, Bill.series == "")).one()
    top = [{"item": f"{i}{'' if v in ('Regular', '') else ' ' + v}", "qty": int(q), "sales_paise": int(s)} for i, v, q, s in db.execute(
        select(BillLine.item_name, BillLine.variant_name, func.sum(BillLine.qty), func.sum(BillLine.taxable_paise))
        .join(Bill, Bill.id == BillLine.bill_id).where(*paid).group_by(BillLine.item_name, BillLine.variant_name)
        .order_by(func.sum(BillLine.qty).desc()).limit(5))]
    names = {u.id: u.name for u in db.scalars(select(User))}
    shifts = []
    ids = set(db.scalars(select(Bill.shift_id).where(Bill.outlet_id == outlet_id, Bill.business_date == day)))
    ids |= set(db.scalars(select(Shift.id).where(Shift.outlet_id == outlet_id, Shift.status == "open")))
    for sh in db.scalars(select(Shift).where(Shift.id.in_(ids)).order_by(Shift.opened_at)) if ids else []:
        so = shift_out(db, sh)
        shifts.append({"cashier": names.get(sh.opened_by, "?"), "status": sh.status, "opening_paise": sh.opening_cash_paise,
                       "expected_paise": so.expected_cash_paise, "counted_paise": so.counted_cash_paise,
                       "difference_paise": so.variance_paise, "bills": so.bills})
    moves = {k: int(v) for k, v in db.execute(select(StockMove.kind, func.sum(StockMove.value_paise)).where(
        StockMove.outlet_id == outlet_id, StockMove.business_date == day).group_by(StockMove.kind))}
    bought = db.scalar(select(func.coalesce(func.sum(Purchase.total_paise), 0)).where(
        Purchase.outlet_id == outlet_id, Purchase.business_date == day, Purchase.status == "posted")) or 0
    cash_out = db.scalar(select(func.coalesce(func.sum(CashMovement.amount_paise), 0)).join(Shift, Shift.id == CashMovement.shift_id).where(
        Shift.outlet_id == outlet_id, CashMovement.kind == "out", func.date(func.timezone("Asia/Kolkata", CashMovement.created_at)) == day)) or 0
    cost = -moves.get("sale", 0)
    return {"bills": int(n), "gross_paise": int(gross), "discount_paise": int(disc), "taxable_paise": int(taxable), "gst_paise": int(gst),
            "round_off_paise": int(ro), "sales_paise": int(total), "by_mode": modes, "cancelled_bills": int(c_n), "cancelled_paise": int(c_v),
            "corrections": int(corrections), "first_bill": first, "last_bill": last, "top_items": top, "shifts": shifts,
            "cost_of_sales_paise": cost, "food_cost_bp": (cost * 10000 + int(taxable) // 2) // int(taxable) if taxable and cost else None,
            "wastage_paise": -moves.get("waste", 0), "count_diff_paise": moves.get("count", 0), "purchases_paise": int(bought),
            "cash_out_paise": int(cash_out)}


@router.get("/{outlet_id}")
def day_status(outlet_id: uuid.UUID, day: date | None = None, auth: Auth = Depends(require(Perm.day_close)),
               db: Session = Depends(get_db)) -> dict:
    ensure_outlet_access(db, auth, outlet_id)
    get_or_404(db, Outlet, outlet_id, "Outlet")
    d = day or business_date()
    rec = db.scalar(select(DayClose).where(DayClose.outlet_id == outlet_id, DayClose.business_date == d))
    show_cost = auth.can(Perm.costing_view)
    s = rec.summary if rec and rec.status == "closed" else summary(db, outlet_id, d)
    if not show_cost:
        s = {k: v for k, v in s.items() if k not in ("cost_of_sales_paise", "food_cost_bp", "wastage_paise", "count_diff_paise")}
    recent = [{"day": str(r.business_date), "status": r.status, "sales_paise": r.summary.get("sales_paise"),
               "closed_by": db.get(User, r.closed_by).name if db.get(User, r.closed_by) else None}
              for r in db.scalars(select(DayClose).where(DayClose.outlet_id == outlet_id).order_by(DayClose.business_date.desc()).limit(10))]
    return {"day": str(d), "today": str(business_date()), "closed": bool(rec and rec.status == "closed"),
            "closed_at": rec.closed_at.isoformat() if rec and rec.status == "closed" else None,
            "closed_by": (db.get(User, rec.closed_by).name if rec and rec.status == "closed" else None),
            "note": rec.note if rec else None, "warnings": rec.warnings if rec and rec.status == "closed" else None,
            "checks": [] if rec and rec.status == "closed" else checks(db, outlet_id, d), "summary": s, "recent": recent}


@router.post("")
def close_day(body: CloseIn, auth: Auth = Depends(require(Perm.day_close)), db: Session = Depends(get_db)) -> dict:
    ensure_outlet_access(db, auth, body.outlet_id)
    outlet = get_or_404(db, Outlet, body.outlet_id, "Outlet")
    if body.day > business_date():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That day hasn't started yet")
    rec = db.scalar(select(DayClose).where(DayClose.outlet_id == outlet.id, DayClose.business_date == body.day).with_for_update())
    if rec and rec.status == "closed":
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.day:%d %b} is already closed")
    open_items = checks(db, outlet.id, body.day)
    blocking = [c for c in open_items if c["block"]]
    if blocking and not body.close_anyway:
        raise HTTPException(status.HTTP_409_CONFLICT, blocking[0]["text"] + ". " + blocking[0]["fix"])
    if blocking and len((body.note or "").strip()) < 5:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Write a note saying why the day is closed with things still open")
    s = summary(db, outlet.id, body.day)
    if rec is None:
        rec = DayClose(tenant_id=auth.tenant_id, outlet_id=outlet.id, business_date=body.day, summary=s, closed_by=auth.user_id)
        db.add(rec)
    rec.status, rec.summary, rec.closed_by = "closed", s, auth.user_id
    rec.warnings = [c["text"] for c in open_items] or None
    rec.note = (body.note or "").strip() or None
    db.flush()
    # The owner's day summary goes now (the 4:30 am run sees the same reference and doesn't repeat it).
    cfg = sms.settings_for(db, auth.tenant_id)
    if cfg and cfg.enabled and cfg.send_daily and s["bills"]:
        vals = sms.daily_values(db, outlet, body.day)
        for phone in cfg.daily_to or []:
            sms.enqueue(db, auth.tenant_id, "daily", phone, vals, ref=f"{outlet.id}:{body.day}:{phone}", outlet_id=outlet.id)
    audit.record(db, auth.tenant_id, auth.user_id, "close", "day", rec.id,
                 after={"outlet": outlet.name, "day": str(body.day), "sales_paise": s["sales_paise"], "warnings": rec.warnings, "note": rec.note})
    return day_status(outlet.id, body.day, auth, db)


@router.post("/reopen")
def reopen_day(body: ReopenIn, auth: Auth = Depends(require(Perm.day_close)), db: Session = Depends(get_db)) -> dict:
    ensure_outlet_access(db, auth, body.outlet_id)
    rec = db.scalar(select(DayClose).where(DayClose.outlet_id == body.outlet_id, DayClose.business_date == body.day))
    if rec is None or rec.status != "closed":
        raise HTTPException(status.HTTP_409_CONFLICT, "That day isn't closed")
    rec.status = "reopened"
    rec.note = f"Reopened: {body.reason.strip()}"
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "reopen", "day", rec.id, after={"day": str(body.day), "reason": body.reason.strip()})
    return day_status(body.outlet_id, body.day, auth, db)
