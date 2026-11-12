"""Bills, payments, cancellations (manager PIN + reason) and cashier shifts."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..core.cash import clean, total_paise
from ..core.billing import Discount, PaymentIn, compute_bill, fiscal_year_start, invoice_number, validate_payments
from ..core.kds import business_date
from ..core.permissions import Perm, Role
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..core.loyalty import check_use, reward
from ..models import Bill, CashMovement, Drawer, Kot, User, LoyaltySettings, Order, Outlet, Shift, Tenant
from ..schemas import (
    BillCreate, BillOut, BillPreviewIn, BillPreviewOut, CancelIn, CashMoveIn, OrderOut, ShiftClose, ShiftOpen,
    ShiftOut,
)
from ..services import alerts, audit, cash_alerts, loyalty, sms, stock
from ..services.approval import verify_manager_pin
from ..services.billing import (
    approval_reasons, bill_out, billable_rows, fy_counter_date, open_shift, preview, save_bill, shift_out, to_discount,
)
from ..services.orders import next_number, order_out
from .common import get_or_404
from .dayclose import is_closed

router = APIRouter(tags=["billing"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_manager(auth: Auth) -> bool:
    """Approves on their own (no PIN needed): owner and manager by default, set under Who can do what."""
    return auth.can(Perm.approve)


def _sees_cash(auth: Auth) -> bool:
    return auth.can(Perm.cash_expected)


def _live_order(db: Session, auth: Auth, order_id: uuid.UUID) -> Order:
    order = get_or_404(db, Order, order_id, "Order")
    ensure_outlet_access(db, auth, order.outlet_id)
    if order.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "This order was cancelled")
    if order.status == "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Accept this QR order first")
    return order


def _loyalty(db: Session, tenant_id: uuid.UUID, loy, discount, phone: str | None, rows, outlet_id: uuid.UUID | None = None) -> tuple[Discount | None, int, int, str | None]:
    """The discount for points / the stamp reward, checked against the customer's balance.
    Returns (discount, points used, reward used, reason text)."""
    if not loy or not (loy.points_paise or loy.use_reward):
        return to_discount(discount), 0, 0, ((discount.reason or "").strip() or None) if discount else None
    if discount and discount.value:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Use either a discount or loyalty on a bill, not both")
    from ..services.training import is_training
    if is_training(db, outlet_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Loyalty is off in training: real customers' points stay untouched")
    if not phone:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Points and rewards need the customer's mobile number")
    r = loyalty.rules(db, tenant_id)
    cust = loyalty.customer(db, tenant_id, phone, create=False)
    if cust is not None:
        loyalty.expire_if_lapsed(db, r, cust)
    gross = sum(x.line.qty * x.line.unit_paise for x in rows)
    bal, stamps = (cust.points_paise, cust.stamps) if cust else (0, 0)
    why = check_use(r, points_paise=loy.points_paise, use_reward=loy.use_reward, balance_paise=bal, stamps=stamps, gross_paise=gross)
    if why:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, why)
    rw = reward(r, stamps, gross) if loy.use_reward else 0
    cfg = db.get(LoyaltySettings, tenant_id)
    parts = ([f"₹{loy.points_paise / 100:g} of points"] if loy.points_paise else []) + ([cfg.reward_label if cfg else "Stamp reward"] if rw else [])
    return Discount("flat", loy.points_paise + rw), loy.points_paise, rw, "Loyalty: " + " + ".join(parts)


@router.post("/bills/preview", response_model=BillPreviewOut)
def bill_preview(body: BillPreviewIn, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    order = _live_order(db, auth, body.order_id)
    tenant = db.get(Tenant, auth.tenant_id)
    rows = billable_rows(db, order)
    disc, lp, _lr, _why = _loyalty(db, auth.tenant_id, body.loyalty, body.discount, body.loyalty.phone if body.loyalty else None, rows)
    try:
        calc = compute_bill([r.line for r in rows], disc)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    needs = [] if _is_manager(auth) or (body.loyalty and calc.discount_paise) else approval_reasons(tenant, calc, [])
    return preview(rows, calc, needs)


@router.post("/bills", response_model=BillOut, status_code=201)
def create_bill(body: BillCreate, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    if body.id is not None and (existing := db.get(Bill, body.id)) is not None:
        return bill_out(db, existing)  # retried request
    order = _live_order(db, auth, body.order_id)
    bill = issue_bill(db, auth, order, body)
    return bill_out(db, bill)


def issue_bill(db: Session, auth: Auth, order: Order, body: BillCreate) -> Bill:
    """Validate and save a GST bill for an order. Shared by the counter and kiosk/QR acceptance."""
    if db.scalar(select(Bill.id).where(Bill.order_id == order.id, Bill.status == "paid")) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This order is already billed")
    shift = open_shift(db, order.outlet_id, auth.user_id)
    if shift is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Open your shift before billing")
    tenant = db.get(Tenant, auth.tenant_id)
    outlet = db.get(Outlet, order.outlet_id)

    c = body.customer
    if not c.phone and not c.declined:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Ask for the customer's mobile number, or tap Declined if they don't want to share it")
    modes = [p.mode for p in body.payments]
    if "due" in modes and not c.phone:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A credit (due) bill needs the customer's mobile number")

    rows = billable_rows(db, order)
    if not rows:
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing left to bill on this order")
    disc, lp, lr, reason = _loyalty(db, auth.tenant_id, body.loyalty, body.discount, c.phone, rows, outlet.id)
    try:
        calc = compute_bill([r.line for r in rows], disc)
        validate_payments(calc.total_paise, [PaymentIn(p.mode, p.amount_paise) for p in body.payments])
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    if calc.discount_paise and not reason:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Give a reason for the discount")

    approver = None
    needs = approval_reasons(tenant, calc, modes) if not (lp or lr) else (["Complimentary bill"] if "complimentary" in modes else [])
    if needs:
        if _is_manager(auth):
            approver = auth.user_id
        else:
            approver = verify_manager_pin(db, auth.tenant_id, order.outlet_id, body.manager_pin).id

    day = business_date()
    if is_closed(db, outlet.id, day):
        raise HTTPException(status.HTTP_409_CONFLICT, "Today has been closed for this outlet. A manager can reopen it under Day close.")
    fy = fiscal_year_start(day, tenant.fy_start_month)
    seq = next_number(db, auth.tenant_id, outlet.id, fy_counter_date(fy, tenant.fy_start_month), "inv")
    try:
        inv = invoice_number(outlet.code, fy, seq)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    bill = Bill(
        id=body.id or uuid.uuid4(), tenant_id=auth.tenant_id, outlet_id=outlet.id, order_id=order.id,
        shift_id=shift.id, business_date=day, fy_start=fy, seq=seq, invoice_no=inv,
        gross_paise=calc.gross_paise, discount_paise=calc.discount_paise,
        discount_reason=reason if calc.discount_paise else None, loyalty_points_paise=lp, loyalty_reward_paise=lr,
        taxable_paise=calc.taxable_paise, cgst_paise=calc.cgst_paise, sgst_paise=calc.sgst_paise,
        igst_paise=calc.igst_paise, round_off_paise=calc.round_off_paise, total_paise=calc.total_paise,
        customer_name=(c.name or "").strip() or None, customer_phone=c.phone, customer_declined_phone=c.declined,
        customer_gstin=c.gstin, created_by=auth.user_id, approved_by=approver,
    )
    save_bill(db, bill, rows, calc, [(p.mode, p.amount_paise, p.reference) for p in body.payments])
    sms.on_bill_created(db, bill)
    alerts.on_bill_created(db, bill, modes)
    audit.record(db, auth.tenant_id, auth.user_id, "create", "bill", bill.id,
                 after={"invoice_no": inv, "total_paise": calc.total_paise, "modes": modes,
                        "approved_by": str(approver) if approver else None})
    return bill


@router.get("/bills", response_model=list[BillOut])
def list_bills(outlet_id: uuid.UUID, day: date | None = None, q: str | None = None,
               auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    ensure_outlet_access(db, auth, outlet_id)
    stmt = select(Bill).where(Bill.outlet_id == outlet_id)
    if q:
        like = f"%{q.strip().upper()}%"
        stmt = stmt.where(or_(Bill.invoice_no.like(like), Bill.customer_phone.like(f"%{q.strip()}%")))
    else:
        stmt = stmt.where(Bill.business_date == (day or business_date()))
    return [bill_out(db, b) for b in db.scalars(stmt.order_by(Bill.created_at.desc(), Bill.seq.desc()).limit(200))]


@router.get("/bills/{bill_id}", response_model=BillOut)
def get_bill(bill_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    bill = get_or_404(db, Bill, bill_id, "Bill")
    ensure_outlet_access(db, auth, bill.outlet_id)
    return bill_out(db, bill)


@router.post("/bills/{bill_id}/cancel", response_model=BillOut)
def cancel_bill(bill_id: uuid.UUID, body: CancelIn, auth: Auth = Depends(require(Perm.billing)),
                db: Session = Depends(get_db)):
    """The invoice number stays in the series marked cancelled (reported in GSTR-1). The order
    can then be billed again under a new number."""
    bill = get_or_404(db, Bill, bill_id, "Bill")
    ensure_outlet_access(db, auth, bill.outlet_id)
    if bill.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "Bill is already cancelled")
    approver = auth.user_id if _is_manager(auth) else verify_manager_pin(db, auth.tenant_id, bill.outlet_id,
                                                                         body.manager_pin).id
    bill.status = "cancelled"
    bill.cancelled_at = _now()
    bill.cancelled_by = auth.user_id
    bill.cancel_approved_by = approver
    bill.cancel_reason = body.reason.strip()
    audit.record(db, auth.tenant_id, auth.user_id, "cancel", "bill", bill.id, {"status": "paid"},
                 {"status": "cancelled", "reason": bill.cancel_reason, "approved_by": str(approver)})
    db.flush()
    alerts.on_bill_cancelled(db, bill)
    stock.resync_bill(db, bill, auth.user_id)   # the stock comes back
    loyalty.resync_bill(db, bill, auth.user_id)  # points earned and used on it are undone
    return bill_out(db, bill)


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: uuid.UUID, body: CancelIn, auth: Auth = Depends(require(Perm.kot_send)),
                 db: Session = Depends(get_db)):
    """Stops the kitchen: every ticket on it is cancelled and disappears from the KDS."""
    order = _live_order(db, auth, order_id)
    if db.scalar(select(Bill.id).where(Bill.order_id == order.id, Bill.status == "paid")) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cancel the bill first, then the order")
    if order.status == "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This order was already served")
    approver = auth.user_id if _is_manager(auth) else verify_manager_pin(db, auth.tenant_id, order.outlet_id,
                                                                         body.manager_pin).id
    for k in db.scalars(select(Kot).where(Kot.order_id == order.id)):
        k.status = "cancelled"
    order.status = "cancelled"
    order.cancelled_at = _now()
    order.cancelled_by = auth.user_id
    order.cancel_approved_by = approver
    order.cancel_reason = body.reason.strip()
    audit.record(db, auth.tenant_id, auth.user_id, "cancel", "order", order.id,
                 after={"reason": order.cancel_reason, "approved_by": str(approver)})
    db.flush()
    return order_out(db, order)


# ------------------------------------------------------------------ shifts

@router.get("/shifts/current", response_model=ShiftOut | None)
def current_shift(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    ensure_outlet_access(db, auth, outlet_id)
    shift = open_shift(db, outlet_id, auth.user_id)
    # Blind count: a cashier doesn't see what the drawer should hold, so the count can't be made to match.
    return shift_out(db, shift, blind=not _sees_cash(auth)) if shift else None


@router.get("/shifts", response_model=list[ShiftOut])
def list_shifts(outlet_id: uuid.UUID, day: date | None = None, auth: Auth = Depends(require(Perm.reports_view)),
                db: Session = Depends(get_db)):
    """All cashiers' shifts at an outlet that day (open ones first), for the manager."""
    ensure_outlet_access(db, auth, outlet_id)
    d = day or business_date()
    shifts = db.scalars(select(Shift).where(Shift.outlet_id == outlet_id).order_by(Shift.opened_at.desc()).limit(100))
    out = [shift_out(db, s) for s in shifts if s.status == "open" or business_date(s.opened_at) == d
           or (s.closed_at is not None and business_date(s.closed_at) == d)]
    return sorted(out, key=lambda s: s.status != "open")


def _own_or_manager(auth: Auth, shift: Shift) -> None:
    if shift.opened_by != auth.user_id and not _sees_cash(auth):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is another cashier's shift")


def pick_drawer(db: Session, tenant_id: uuid.UUID, outlet_id: uuid.UUID, drawer_id: uuid.UUID | None,
                user_id: uuid.UUID | None = None) -> Drawer:
    """The drawer for a new shift: the one asked for, else the first free one. A drawer in someone's
    open shift can't be taken (two cashiers' cash would mix)."""
    drawers = list(db.scalars(select(Drawer).where(Drawer.outlet_id == outlet_id, Drawer.is_active.is_(True)).order_by(Drawer.name)))
    if not drawers:
        d = Drawer(tenant_id=tenant_id, outlet_id=outlet_id, name="Drawer 1")
        db.add(d)
        db.flush()
        drawers = [d]
    busy = {s.drawer_id: s for s in db.scalars(select(Shift).where(Shift.outlet_id == outlet_id, Shift.status == "open"))}
    names = {u.id: u.name for u in db.scalars(select(User))}
    if drawer_id is not None:
        asked = next((x for x in drawers if x.id == drawer_id), None)
        if asked is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That drawer isn't in use at this outlet")
        if asked.id in busy:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{asked.name} is in {names.get(busy[asked.id].opened_by, 'another')}'s shift. Pick another drawer.")
        return asked
    free = [x for x in drawers if x.id not in busy]
    if not free:
        # Another cashier starts at the same time: they get their own drawer, so the two counts stay apart.
        n = len(list(db.scalars(select(Drawer.id).where(Drawer.outlet_id == outlet_id)))) + 1
        if n > 12:
            users = ", ".join(f"{x.name}: {names.get(busy[x.id].opened_by, '?')}" for x in drawers)
            raise HTTPException(status.HTTP_409_CONFLICT, f"Every drawer is in use ({users}).")
        d = Drawer(tenant_id=tenant_id, outlet_id=outlet_id, name=f"Drawer {n}")
        db.add(d)
        db.flush()
        return d
    if user_id is not None:   # the drawer this cashier used last, if it's free
        last = db.scalar(select(Shift.drawer_id).where(Shift.outlet_id == outlet_id, Shift.opened_by == user_id, Shift.drawer_id.is_not(None))
                         .order_by(Shift.opened_at.desc()).limit(1))
        mine = next((x for x in free if x.id == last), None)
        if mine:
            return mine
    return free[0]


@router.get("/outlets/{outlet_id}/drawers")
def list_drawers(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> list[dict]:
    ensure_outlet_access(db, auth, outlet_id)
    busy = {s.drawer_id: s for s in db.scalars(select(Shift).where(Shift.outlet_id == outlet_id, Shift.status == "open"))}
    names = {u.id: u.name for u in db.scalars(select(User))}
    return [{"id": str(d.id), "name": d.name, "is_active": d.is_active,
             "in_use_by": names.get(busy[d.id].opened_by) if d.id in busy else None}
            for d in db.scalars(select(Drawer).where(Drawer.outlet_id == outlet_id).order_by(Drawer.name))]


class DrawerIn(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    is_active: bool = True


@router.post("/outlets/{outlet_id}/drawers", status_code=201)
def add_drawer(outlet_id: uuid.UUID, body: DrawerIn, auth: Auth = Depends(require(Perm.day_close)), db: Session = Depends(get_db)) -> dict:
    ensure_outlet_access(db, auth, outlet_id)
    get_or_404(db, Outlet, outlet_id, "Outlet")
    if db.scalar(select(Drawer.id).where(Drawer.outlet_id == outlet_id, func.lower(Drawer.name) == body.name.strip().lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a drawer called {body.name.strip()}")
    d = Drawer(tenant_id=auth.tenant_id, outlet_id=outlet_id, name=body.name.strip(), is_active=body.is_active)
    db.add(d)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "drawer", d.id, after={"name": d.name})
    return {"id": str(d.id), "name": d.name, "is_active": d.is_active, "in_use_by": None}


@router.patch("/drawers/{drawer_id}")
def edit_drawer(drawer_id: uuid.UUID, body: DrawerIn, auth: Auth = Depends(require(Perm.day_close)), db: Session = Depends(get_db)) -> dict:
    d = get_or_404(db, Drawer, drawer_id, "Drawer")
    ensure_outlet_access(db, auth, d.outlet_id)
    if not body.is_active and db.scalar(select(Shift.id).where(Shift.drawer_id == d.id, Shift.status == "open")):
        raise HTTPException(status.HTTP_409_CONFLICT, "This drawer is in an open shift. Close the shift first.")
    d.name, d.is_active = body.name.strip(), body.is_active
    db.flush()
    return {"id": str(d.id), "name": d.name, "is_active": d.is_active, "in_use_by": None}


@router.post("/shifts", response_model=ShiftOut, status_code=201)
def start_shift(body: ShiftOpen, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    get_or_404(db, Outlet, body.outlet_id, "Outlet")
    ensure_outlet_access(db, auth, body.outlet_id)
    if open_shift(db, body.outlet_id, auth.user_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Your shift is already open at this outlet")
    opening = total_paise(body.opening_denoms) if body.opening_denoms is not None else body.opening_cash_paise
    drawer = pick_drawer(db, auth.tenant_id, body.outlet_id, body.drawer_id, auth.user_id)
    # Carry forward: the cash this drawer's last close left in it is what should be there now.
    prev = cash_alerts.previous_close(db, body.outlet_id, drawer.id)
    if prev is None and not db.scalar(select(Shift.id).where(Shift.drawer_id == drawer.id).limit(1)):
        prev = cash_alerts.previous_close(db, body.outlet_id, None)   # shifts from before drawers existed
    carried = None
    if prev is not None:
        carried = prev.counted_cash_paise if prev.counted_cash_paise is not None else prev.expected_cash_paise
    shift = Shift(tenant_id=auth.tenant_id, outlet_id=body.outlet_id, opened_by=auth.user_id, drawer_id=drawer.id,
                  opening_cash_paise=opening, opening_denoms=body.opening_denoms,
                  carried_from_id=prev.id if prev else None, expected_opening_paise=carried)
    db.add(shift)
    db.flush()
    db.refresh(shift)
    alert = cash_alerts.check(db, shift, "open", carried, opening, body.opening_denoms) if carried is not None else None
    audit.record(db, auth.tenant_id, auth.user_id, "open", "shift", shift.id,
                 after={"opening": opening, "denoms": body.opening_denoms})
    out = shift_out(db, shift, blind=not _sees_cash(auth))
    out.manager_alerted = alert is not None
    if alert is not None:
        out.opening_difference_paise = alert.counted_paise - alert.expected_paise
    return out


@router.post("/shifts/{shift_id}/cash", response_model=ShiftOut)
def cash_movement(shift_id: uuid.UUID, body: CashMoveIn, auth: Auth = Depends(require(Perm.billing)),
                  db: Session = Depends(get_db)):
    shift = get_or_404(db, Shift, shift_id, "Shift")
    ensure_outlet_access(db, auth, shift.outlet_id)
    if shift.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "This shift is closed")
    _own_or_manager(auth, shift)
    try:
        denoms = clean(body.denoms) if body.denoms else None
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    amount = total_paise(denoms) if denoms else body.amount_paise
    if amount <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter the amount, or count the notes and coins")
    db.add(CashMovement(tenant_id=auth.tenant_id, shift_id=shift.id, kind=body.kind, amount_paise=amount, denoms=denoms,
                        reason=body.reason.strip(), created_by=auth.user_id))
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, f"cash_{body.kind}", "shift", shift.id,
                 after={"amount": amount, "denoms": denoms, "reason": body.reason})
    return shift_out(db, shift, blind=not _sees_cash(auth))


@router.post("/shifts/{shift_id}/close", response_model=ShiftOut)
def close_shift(shift_id: uuid.UUID, body: ShiftClose, auth: Auth = Depends(require(Perm.billing)),
                db: Session = Depends(get_db)):
    shift = get_or_404(db, Shift, shift_id, "Shift")
    ensure_outlet_access(db, auth, shift.outlet_id)
    if shift.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "This shift is already closed")
    _own_or_manager(auth, shift)   # a manager can close for a cashier who has left; it's recorded as theirs
    summary = shift_out(db, shift)
    expected = summary.expected_cash_paise or 0
    shift.expected_cash_paise = expected
    shift.counted_cash_paise = total_paise(body.counted_denoms) if body.counted_denoms is not None else body.counted_cash_paise
    shift.counted_denoms = body.counted_denoms
    shift.status = "closed"
    shift.closed_at = _now()
    shift.closed_by = auth.user_id
    db.flush()
    alert = cash_alerts.check(db, shift, "close", expected, shift.counted_cash_paise, body.counted_denoms)
    out = shift_out(db, shift)
    out.manager_alerted = alert is not None
    audit.record(db, auth.tenant_id, auth.user_id, "close", "shift", shift.id,
                 after={"expected": out.expected_cash_paise, "counted": out.counted_cash_paise,
                        "variance": out.variance_paise})
    return out


# ------------------------------------------------------------------ owner alerts (shown on Home)

@router.get("/alerts")
def list_alerts(days: int = 7, auth: Auth = Depends(require(Perm.approve)), db: Session = Depends(get_db)) -> dict:
    from ..models import OwnerAlert, Outlet as _Outlet, UserOutlet
    from ..core.permissions import Role
    since = _now() - timedelta(days=max(1, min(days, 31)))
    q = select(OwnerAlert).where(OwnerAlert.created_at >= since)
    if auth.role != Role.owner:      # managers: their outlets, and box-wide ones
        mine = select(UserOutlet.outlet_id).where(UserOutlet.user_id == auth.user_id)
        q = q.where(or_(OwnerAlert.outlet_id.is_(None), OwnerAlert.outlet_id.in_(mine)))
    rows = list(db.scalars(q.order_by(OwnerAlert.created_at.desc()).limit(100)))
    names = {o.id: o.name for o in db.scalars(select(_Outlet))}
    return {"unseen": sum(1 for a in rows if a.seen_at is None),
            "alerts": [{"id": str(a.id), "kind": a.kind, "text": a.text, "outlet": names.get(a.outlet_id) if a.outlet_id else None,
                        "at": a.created_at.isoformat(), "seen": a.seen_at is not None} for a in rows]}


@router.post("/alerts/seen")
def alerts_seen(auth: Auth = Depends(require(Perm.approve)), db: Session = Depends(get_db)) -> dict:
    from ..models import OwnerAlert
    n = 0
    for a in db.scalars(select(OwnerAlert).where(OwnerAlert.seen_at.is_(None))):
        a.seen_at = _now()
        n += 1
    return {"seen": n}
