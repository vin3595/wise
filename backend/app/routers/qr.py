"""QR ordering: customers scan the table (or takeaway) QR, order from their phone, and staff accept
it into the kitchen. Public endpoints need no login; the outlet's qr_key in the link identifies it."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.kds import business_date, normalize_car_number
from ..core.permissions import Perm
from ..db import system_session, tenant_session
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..models import Order, Outlet, Tenant
from ..core.billing import compute_bill
from ..schemas import (
    AcceptPaidIn, BillCreate, BillOut, CustomerIn, KioskOrderIn, KioskQuoteIn, OrderLineIn, OrderOut, PaymentLineIn,
    QrOrderIn, QrOrderStatus, RejectIn,
)
from ..services.billing import bill_out, billable_rows, quote_lines, upi_link
from .billing import issue_bill
from ..services import audit
from ..services.menu_view import build_outlet_menu
from ..services.orders import (
    add_kots, assign_token, lock_table, open_table_order, order_out, orders_out, refresh_order_status, resolve_lines,
)
from .common import get_or_404

router = APIRouter(tags=["qr-ordering"])

MAX_OPEN_QR_ORDERS_PER_PHONE = 3
QR_WINDOW_HOURS = 6


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _outlet_by_key(qr_key: str, kiosk: bool = False) -> tuple[uuid.UUID, uuid.UUID]:
    with system_session() as db:
        outlet = db.scalar(select(Outlet).where(Outlet.qr_key == qr_key))
        enabled = outlet is not None and (outlet.kiosk_enabled if kiosk else outlet.qr_ordering)
        if outlet is None or not outlet.is_active or not enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ordering from this QR isn't available right now")
        tenant = db.get(Tenant, outlet.tenant_id)
        if tenant is None or tenant.status != "active":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ordering from this QR isn't available right now")
        return outlet.id, outlet.tenant_id


@router.get("/public/qr/{qr_key}")
def qr_menu(qr_key: str, t: str | None = None) -> dict:
    outlet_id, tenant_id = _outlet_by_key(qr_key)
    with tenant_session(tenant_id) as db:
        outlet = db.get(Outlet, outlet_id)
        tenant = db.get(Tenant, tenant_id)
        table = (t or "").strip().upper() or None
        if table and table not in (outlet.table_labels or []):
            table = None
        menu = build_outlet_menu(db, outlet, hide_sold_out=True)
        return {
            "outlet_name": outlet.name, "app_name": tenant.app_name or tenant.name, "logo_url": tenant.logo_url,
            "primary_color": tenant.primary_color, "table": table, "car_number_required": outlet.car_number_required,
            "show_powered_by": tenant.show_powered_by or tenant.plan != "chain",
            "menu": menu.model_dump(mode="json"),
        }


def _status(order: Order, total: int, upi: str | None = None) -> QrOrderStatus:
    pending_msg = "Waiting for the restaurant to confirm your order"
    if order.payment_status == "awaiting":
        pending_msg = ("Scan the QR with any UPI app to pay" if order.pay_method == "upi"
                       else f"Please pay at the counter. Show code {order.pay_ref}")
    msg = {
        "pending": pending_msg,
        "new": "Order confirmed. It's in the kitchen queue",
        "preparing": "Your order is being prepared",
        "ready": "Ready! Please collect it" if order.order_type != "dine_in" else "Ready, coming to your table",
        "completed": "Enjoy your meal!",
        "cancelled": f"Sorry, the restaurant couldn't take this order: {order.cancel_reason or ''}".strip(),
    }.get(order.status)
    return QrOrderStatus(order_id=order.id, status=order.status, token_no=order.token_no, table=order.table_label,
                         message=msg, total_paise=order.quoted_total_paise or total,
                         payment_status=order.payment_status, pay_ref=order.pay_ref,
                         upi_link=upi if order.payment_status == "awaiting" and order.pay_method == "upi" else None)


def _accept(db: Session, order: Order, outlet: Outlet) -> Order:
    """Send a pending QR order to the kitchen. Dine-in joins the table's open order if there is one."""
    lines = [OrderLineIn(**{k: p[k] for k in ("variant_id", "qty", "addon_ids", "notes")})
             for p in (order.pending_items or [])]
    resolved = resolve_lines(db, outlet, lines)
    target = order
    if order.order_type == "dine_in" and order.table_label:
        lock_table(db, outlet.id, order.table_label)
        existing = open_table_order(db, outlet.id, order.table_label)
        if existing is not None and existing.id != order.id:
            target = existing
    if target is order:
        order.status = "new"
        assign_token(db, order)
        add_kots(db, order, resolved)
    else:
        add_kots(db, target, resolved)
        refresh_order_status(db, target)
        # The QR order itself is closed; the customer's status page follows the table's order.
        order.status = "completed"
        order.completed_at = _now()
        order.merged_into_id = target.id
    return target


@router.post("/public/qr/{qr_key}/orders", response_model=QrOrderStatus, status_code=201)
def place_qr_order(qr_key: str, body: QrOrderIn) -> QrOrderStatus:
    outlet_id, tenant_id = _outlet_by_key(qr_key)
    with tenant_session(tenant_id) as db:
        outlet = db.get(Outlet, outlet_id)
        table = (body.table or "").strip().upper() or None
        if table and table not in (outlet.table_labels or []):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Scan the QR on your table again")
        order_type = "dine_in" if table and not body.takeaway else "takeaway"
        try:
            car = normalize_car_number(body.car_number) if order_type == "takeaway" else None
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
        if order_type == "takeaway" and outlet.car_number_required and not car:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter your car number")
        open_count = db.scalar(select(func.count()).select_from(Order).where(
            Order.outlet_id == outlet.id, Order.customer_phone == body.customer_phone,
            Order.status.in_(("pending", "new", "preparing")),
            Order.created_at >= _now() - timedelta(hours=QR_WINDOW_HOURS)))
        if open_count and open_count >= MAX_OPEN_QR_ORDERS_PER_PHONE:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "You already have orders in progress. Please ask the staff for help.")
        resolved = resolve_lines(db, outlet, body.items)  # checks availability and prices now
        pending = [{
            "variant_id": str(r.variant.id), "qty": r.line.qty, "addon_ids": [str(a) for a in r.line.addon_ids],
            "notes": (r.line.notes or "").strip() or None, "item_name": r.item.name, "variant_name": r.variant.name,
            "addon_names": [a["name"] for a in r.addons],
            "unit_paise": r.price_paise + sum(a["price_paise"] for a in r.addons),
        } for r in resolved]
        order = Order(
            tenant_id=tenant_id, outlet_id=outlet.id, business_date=business_date(), order_type=order_type,
            table_label=table if order_type == "dine_in" else None, car_number=car,
            customer_name=body.customer_name.strip(), customer_phone=body.customer_phone,
            notes=(body.notes or "").strip() or None, source="qr", status="pending", pending_items=pending,
        )
        db.add(order)
        db.flush()
        if outlet.qr_auto_accept:
            _accept(db, order, outlet)
        audit.record(db, tenant_id, None, "qr_order", "order", order.id,
                     after={"table": table, "auto_accepted": outlet.qr_auto_accept})
        total = sum(p["qty"] * p["unit_paise"] for p in pending)
        return _status(order, total)


@router.get("/public/qr/{qr_key}/orders/{order_id}", response_model=QrOrderStatus)
def qr_order_status(qr_key: str, order_id: uuid.UUID, kiosk: bool = False) -> QrOrderStatus:
    """The customer's phone (or the kiosk) polls this. The order id is a random UUID only they have."""
    outlet_id, tenant_id = _outlet_by_key(qr_key, kiosk)
    with tenant_session(tenant_id) as db:
        order = db.get(Order, order_id)
        if order is None or order.outlet_id != outlet_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
        total = sum(p["qty"] * p.get("unit_paise", 0) for p in (order.pending_items or []))
        if order.merged_into_id is not None:
            target = db.get(Order, order.merged_into_id)
            if target is not None:
                st = _status(target, total)
                st.order_id = order.id
                return st
        return _status(order, total, _upi(db, order))


def _upi(db: Session, order: Order) -> str | None:
    outlet = db.get(Outlet, order.outlet_id)
    tenant = db.get(Tenant, order.tenant_id)
    if not order.quoted_total_paise:
        return None
    return upi_link(outlet, tenant, order.quoted_total_paise, f"Order {order.pay_ref}")


# ------------------------------------------------------------------ self-order kiosk

@router.get("/public/kiosk/{qr_key}")
def kiosk_menu(qr_key: str) -> dict:
    outlet_id, tenant_id = _outlet_by_key(qr_key, kiosk=True)
    with tenant_session(tenant_id) as db:
        outlet = db.get(Outlet, outlet_id)
        tenant = db.get(Tenant, tenant_id)
        return {
            "outlet_name": outlet.name, "app_name": tenant.app_name or tenant.name, "logo_url": tenant.logo_url,
            "primary_color": tenant.primary_color, "car_number_required": outlet.car_number_required,
            "upi_available": bool(outlet.upi_vpa), "show_powered_by": tenant.show_powered_by or tenant.plan != "chain",
            "menu": build_outlet_menu(db, outlet, hide_sold_out=True).model_dump(mode="json"),
        }


@router.post("/public/kiosk/{qr_key}/quote")
def kiosk_quote(qr_key: str, body: KioskQuoteIn) -> dict:
    """Total with GST before the customer pays."""
    outlet_id, tenant_id = _outlet_by_key(qr_key, kiosk=True)
    with tenant_session(tenant_id) as db:
        calc = quote_lines(db, resolve_lines(db, db.get(Outlet, outlet_id), body.items))
        return {"subtotal_paise": calc.gross_paise, "tax_paise": calc.tax_paise, "round_off_paise": calc.round_off_paise,
                "total_paise": calc.total_paise}


@router.post("/public/kiosk/{qr_key}/orders", response_model=QrOrderStatus, status_code=201)
def kiosk_order(qr_key: str, body: KioskOrderIn) -> QrOrderStatus:
    outlet_id, tenant_id = _outlet_by_key(qr_key, kiosk=True)
    with tenant_session(tenant_id) as db:
        outlet = db.get(Outlet, outlet_id)
        if body.pay == "upi" and not outlet.upi_vpa:
            raise HTTPException(status.HTTP_409_CONFLICT, "UPI isn't set up here. Choose Pay at counter.")
        try:
            car = normalize_car_number(body.car_number) if body.order_type == "takeaway" else None
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
        if body.order_type == "takeaway" and outlet.car_number_required and not car:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter your car number")
        resolved = resolve_lines(db, outlet, body.items)
        calc = quote_lines(db, resolved)
        pending = [{
            "variant_id": str(r.variant.id), "qty": r.line.qty, "addon_ids": [str(a) for a in r.line.addon_ids],
            "notes": (r.line.notes or "").strip() or None, "item_name": r.item.name, "variant_name": r.variant.name,
            "addon_names": [a["name"] for a in r.addons],
            "unit_paise": r.price_paise + sum(a["price_paise"] for a in r.addons),
        } for r in resolved]
        order = Order(
            tenant_id=tenant_id, outlet_id=outlet.id, business_date=business_date(), order_type=body.order_type,
            car_number=car, customer_name=body.customer_name.strip(), customer_phone=body.customer_phone,
            source="kiosk", status="pending", pending_items=pending, payment_status="awaiting",
            pay_method=body.pay, quoted_total_paise=calc.total_paise, pay_ref=_pay_ref(),
        )
        db.add(order)
        db.flush()
        audit.record(db, tenant_id, None, "kiosk_order", "order", order.id,
                     after={"total_paise": calc.total_paise, "pay": body.pay})
        return _status(order, calc.total_paise, _upi(db, order))


def _pay_ref() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid misreading
    return "K" + "".join(secrets.choice(alphabet) for _ in range(4))


# ------------------------------------------------------------------ staff side

@router.get("/orders-pending", response_model=list[OrderOut])
def pending_orders(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.kot_send)), db: Session = Depends(get_db)):
    ensure_outlet_access(db, auth, outlet_id)
    rows = db.scalars(select(Order).where(Order.outlet_id == outlet_id, Order.status == "pending")
                      .order_by(Order.created_at))
    return orders_out(db, list(rows))


@router.post("/orders/{order_id}/accept-paid", response_model=BillOut)
def accept_paid(order_id: uuid.UUID, body: AcceptPaidIn, auth: Auth = Depends(require(Perm.billing)),
                db: Session = Depends(get_db)):
    """Kiosk order: the cashier confirms the money arrived (UPI soundbox / cash taken). This bills it
    under the cashier's shift and sends the KOTs to the kitchen in one step."""
    order = get_or_404(db, Order, order_id, "Order")
    ensure_outlet_access(db, auth, order.outlet_id)
    if order.status != "pending" or order.payment_status != "awaiting":
        raise HTTPException(status.HTTP_409_CONFLICT, "This order was already handled")
    outlet = db.get(Outlet, order.outlet_id)
    target = _accept(db, order, outlet)
    db.flush()
    calc = compute_bill([r.line for r in billable_rows(db, target)])
    bill = issue_bill(db, auth, target, BillCreate(
        order_id=target.id, customer=CustomerIn(name=order.customer_name, phone=order.customer_phone),
        payments=[PaymentLineIn(mode=body.mode, amount_paise=calc.total_paise, reference=body.reference or order.pay_ref)],
    ))
    order.payment_status = "paid"
    order.created_by = auth.user_id
    audit.record(db, auth.tenant_id, auth.user_id, "accept_paid", "order", order.id,
                 after={"bill": bill.invoice_no, "mode": body.mode, "quoted": order.quoted_total_paise,
                        "billed": bill.total_paise})
    return bill_out(db, bill)


@router.post("/orders/{order_id}/accept", response_model=OrderOut)
def accept_order(order_id: uuid.UUID, auth: Auth = Depends(require(Perm.kot_send)), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    ensure_outlet_access(db, auth, order.outlet_id)
    if order.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This order was already handled")
    if order.payment_status == "awaiting":
        raise HTTPException(status.HTTP_409_CONFLICT, "Confirm the payment for this kiosk order first")
    target = _accept(db, order, db.get(Outlet, order.outlet_id))
    order.created_by = auth.user_id
    audit.record(db, auth.tenant_id, auth.user_id, "accept", "order", order.id,
                 after={"token": target.token_no, "merged_into": str(target.id) if target is not order else None})
    db.flush()
    out = order_out(db, target)
    out.merged_into_existing = target is not order
    return out


@router.post("/orders/{order_id}/reject", response_model=OrderOut)
def reject_order(order_id: uuid.UUID, body: RejectIn, auth: Auth = Depends(require(Perm.kot_send)),
                 db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    ensure_outlet_access(db, auth, order.outlet_id)
    if order.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "This order was already handled")
    order.status = "cancelled"
    order.cancel_reason = body.reason.strip()
    order.cancelled_at = _now()
    order.cancelled_by = auth.user_id
    audit.record(db, auth.tenant_id, auth.user_id, "reject", "order", order.id, after={"reason": order.cancel_reason})
    db.flush()
    return order_out(db, order)
