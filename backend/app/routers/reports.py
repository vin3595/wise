"""Sales summary, item-wise sales, captain/table-wise sales, cancellations and bill corrections."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.kds import business_date
from ..core.permissions import Perm, Role
from ..deps import Auth, get_db, require
from ..models import Bill, BillLine, BillRevision, Order, Outlet, Payment, User, UserOutlet

router = APIRouter(prefix="/reports", tags=["reports"])
MAX_DAYS = 400


def _outlets(db: Session, auth: Auth, outlet_id: uuid.UUID | None, allow_training: bool = True) -> list[uuid.UUID]:
    """Outlets a report covers. 'All outlets' never includes training outlets; one can be picked on its own
    (to look at practice), except for GST and accounts exports."""
    from ..services.training import is_training, real_outlet_ids
    if outlet_id is not None and is_training(db, outlet_id) and not allow_training:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Training outlets have no real bills to export")
    if auth.role == Role.owner:
        allowed = list(db.scalars(select(Outlet.id)))
    else:
        allowed = list(db.scalars(select(UserOutlet.outlet_id).where(UserOutlet.user_id == auth.user_id)))
    if outlet_id is not None:
        if outlet_id not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this outlet")
        return [outlet_id]
    return real_outlet_ids(db, allowed)


def _range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    d2 = date_to or business_date()
    d1 = date_from or d2
    if d1 > d2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Start date is after end date")
    if (d2 - d1).days > MAX_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Pick at most {MAX_DAYS} days")
    return d1, d2


@router.get("/summary")
def summary(outlet_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
            auth: Auth = Depends(require(Perm.reports_view)), db: Session = Depends(get_db)) -> dict:
    outlets = _outlets(db, auth, outlet_id)
    d1, d2 = _range(date_from, date_to)
    base = [Bill.outlet_id.in_(outlets), Bill.business_date >= d1, Bill.business_date <= d2]
    paid = base + [Bill.status == "paid"]
    agg = db.execute(select(
        func.count(), func.coalesce(func.sum(Bill.gross_paise), 0), func.coalesce(func.sum(Bill.discount_paise), 0),
        func.coalesce(func.sum(Bill.taxable_paise), 0), func.coalesce(func.sum(Bill.cgst_paise), 0),
        func.coalesce(func.sum(Bill.sgst_paise), 0), func.coalesce(func.sum(Bill.igst_paise), 0),
        func.coalesce(func.sum(Bill.round_off_paise), 0), func.coalesce(func.sum(Bill.total_paise), 0),
    ).where(*paid)).one()
    bills, gross, disc, taxable, cgst, sgst, igst, ro, total = (int(x) for x in agg)
    cancelled = db.scalar(select(func.count()).select_from(Bill).where(*base, Bill.status == "cancelled")) or 0
    by_mode = {m: int(a) for m, a in db.execute(
        select(Payment.mode, func.sum(Payment.amount_paise)).join(Bill, Bill.id == Payment.bill_id)
        .where(*paid).group_by(Payment.mode))}
    by_type = {t: {"bills": int(n), "sales_paise": int(s)} for t, n, s in db.execute(
        select(Order.order_type, func.count(), func.sum(Bill.total_paise)).join(Order, Order.id == Bill.order_id)
        .where(*paid).group_by(Order.order_type))}
    by_day = [{"date": str(d), "bills": int(n), "sales_paise": int(s)} for d, n, s in db.execute(
        select(Bill.business_date, func.count(), func.sum(Bill.total_paise)).where(*paid)
        .group_by(Bill.business_date).order_by(Bill.business_date))]
    by_outlet = [{"outlet": name, "bills": int(n), "sales_paise": int(s)} for name, n, s in db.execute(
        select(Outlet.name, func.count(), func.sum(Bill.total_paise)).join(Outlet, Outlet.id == Bill.outlet_id)
        .where(*paid).group_by(Outlet.name).order_by(func.sum(Bill.total_paise).desc()))]
    tax_rates = [{"rate_bp": int(r), "taxable_paise": int(t), "cgst_paise": int(c), "sgst_paise": int(s),
                  "igst_paise": int(i)} for r, t, c, s, i in db.execute(
        select(BillLine.rate_bp, func.sum(BillLine.taxable_paise), func.sum(BillLine.cgst_paise),
               func.sum(BillLine.sgst_paise), func.sum(BillLine.igst_paise))
        .join(Bill, Bill.id == BillLine.bill_id).where(*paid).group_by(BillLine.rate_bp).order_by(BillLine.rate_bp))]
    return {
        "date_from": str(d1), "date_to": str(d2), "bills": bills, "cancelled_bills": int(cancelled),
        "gross_paise": gross, "discount_paise": disc, "taxable_paise": taxable, "cgst_paise": cgst,
        "sgst_paise": sgst, "igst_paise": igst, "round_off_paise": ro, "sales_paise": total,
        "avg_bill_paise": total // bills if bills else 0, "by_mode": by_mode, "by_order_type": by_type,
        "by_day": by_day, "by_outlet": by_outlet, "tax_by_rate": tax_rates,
    }


@router.get("/items")
def item_wise(outlet_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
              auth: Auth = Depends(require(Perm.reports_view)), db: Session = Depends(get_db)) -> list[dict]:
    """Item-wise sales from paid bills: quantity, gross, discount, taxable value, tax."""
    outlets = _outlets(db, auth, outlet_id)
    d1, d2 = _range(date_from, date_to)
    rows = db.execute(
        select(BillLine.item_name, BillLine.variant_name, func.sum(BillLine.qty), func.sum(BillLine.gross_paise),
               func.sum(BillLine.discount_paise), func.sum(BillLine.taxable_paise),
               func.sum(BillLine.cgst_paise + BillLine.sgst_paise + BillLine.igst_paise))
        .join(Bill, Bill.id == BillLine.bill_id)
        .where(Bill.outlet_id.in_(outlets), Bill.business_date >= d1, Bill.business_date <= d2, Bill.status == "paid")
        .group_by(BillLine.item_name, BillLine.variant_name)
        .order_by(func.sum(BillLine.qty).desc())
    )
    return [{"item": i, "variant": v, "qty": int(q), "gross_paise": int(g), "discount_paise": int(d),
             "taxable_paise": int(t), "tax_paise": int(x)} for i, v, q, g, d, t, x in rows]


@router.get("/staff")
def staff_wise(outlet_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
               auth: Auth = Depends(require(Perm.reports_view)), db: Session = Depends(get_db)) -> dict:
    """Captain-wise (who took the order) and table-wise sales."""
    outlets = _outlets(db, auth, outlet_id)
    d1, d2 = _range(date_from, date_to)
    where = [Bill.outlet_id.in_(outlets), Bill.business_date >= d1, Bill.business_date <= d2, Bill.status == "paid"]
    captains = [{"name": n or "QR / unknown", "role": r, "orders": int(c), "sales_paise": int(s)}
                for n, r, c, s in db.execute(
        select(User.name, User.role, func.count(), func.sum(Bill.total_paise))
        .select_from(Bill).join(Order, Order.id == Bill.order_id).outerjoin(User, User.id == Order.created_by)
        .where(*where).group_by(User.name, User.role).order_by(func.sum(Bill.total_paise).desc()))]
    tables = [{"table": t, "orders": int(c), "sales_paise": int(s)} for t, c, s in db.execute(
        select(Order.table_label, func.count(), func.sum(Bill.total_paise)).join(Order, Order.id == Bill.order_id)
        .where(*where, Order.table_label.is_not(None)).group_by(Order.table_label)
        .order_by(func.sum(Bill.total_paise).desc()))]
    return {"captains": captains, "tables": tables}


@router.get("/cancellations")
def cancellations(outlet_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
                  auth: Auth = Depends(require(Perm.reports_view)), db: Session = Depends(get_db)) -> list[dict]:
    outlets = _outlets(db, auth, outlet_id)
    d1, d2 = _range(date_from, date_to)
    names = {u.id: u.name for u in db.scalars(select(User))}
    out = []
    for b in db.scalars(select(Bill).where(Bill.outlet_id.in_(outlets), Bill.business_date >= d1,
                                           Bill.business_date <= d2, Bill.status == "cancelled")):
        out.append({"kind": "bill", "ref": b.invoice_no, "date": str(b.business_date), "amount_paise": b.total_paise,
                    "reason": b.cancel_reason, "by": names.get(b.cancelled_by), "approved_by": names.get(b.cancel_approved_by),
                    "at": b.cancelled_at.isoformat() if b.cancelled_at else None})
    for o in db.scalars(select(Order).where(Order.outlet_id.in_(outlets), Order.business_date >= d1,
                                            Order.business_date <= d2, Order.status == "cancelled")):
        out.append({"kind": "qr_rejected" if o.source == "qr" and o.token_no is None else "order",
                    "ref": f"Token #{o.token_no}" if o.token_no else "QR order", "date": str(o.business_date),
                    "amount_paise": None, "reason": o.cancel_reason, "by": names.get(o.cancelled_by),
                    "approved_by": names.get(o.cancel_approved_by),
                    "at": o.cancelled_at.isoformat() if o.cancelled_at else None})
    return sorted(out, key=lambda r: r["at"] or "", reverse=True)


@router.get("/bill-edits")
def bill_edits(outlet_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
               auth: Auth = Depends(require(Perm.reports_view)), db: Session = Depends(get_db)) -> list[dict]:
    """Every correction made in the period (by when it was made), with the bill's total before and after."""
    outlets = _outlets(db, auth, outlet_id)
    d1, d2 = _range(date_from, date_to)
    names = {u.id: u.name for u in db.scalars(select(User))}
    rows = db.execute(
        select(BillRevision, Bill.invoice_no, Bill.business_date).join(Bill, Bill.id == BillRevision.bill_id)
        .where(Bill.outlet_id.in_(outlets), func.date(func.timezone("Asia/Kolkata", BillRevision.created_at)) >= d1,
               func.date(func.timezone("Asia/Kolkata", BillRevision.created_at)) <= d2)
        .order_by(BillRevision.created_at.desc()))
    return [{"invoice_no": inv, "bill_date": str(day), "at": r.created_at.isoformat(), "version": r.revision_no + 1,
             "changes": r.changes, "reason": r.reason, "by": names.get(r.edited_by),
             "approved_by": names.get(r.approved_by) if r.approved_by else None,
             "old_total_paise": r.old_total_paise, "new_total_paise": r.new_total_paise,
             "difference_paise": r.new_total_paise - r.old_total_paise, "bill_id": str(r.bill_id)}
            for r, inv, day in rows]
