"""Orders (sent to the kitchen as KOTs), the kitchen display (KDS) and the customer order-ready screen.

Billing (payments, invoice numbers, GST on the bill) is added on top of these orders in Phase 2.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.kds import TransitionError, business_date, car_matches, check_transition, normalize_car_number
from ..core.permissions import Perm, Role
from ..db import system_session
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..models import Bill, Kot, Order, OrderRequest, Outlet, PrepArea, Tenant, User, UserOutlet
from ..schemas import (
    AddItems, DisplayOrder, QuoteIn, DisplayOut, KotOut, OrderCreate, OrderOut, OutletOut, PrepAreaIn, PrepAreaOut,
    StatusChange,
)
from ..services import sms, audit
from ..services.orders import (add_kots, kot_out, lines_by_kot, lock_table, move_items, new_order_like, next_split_label,
                               open_table_order, order_out, orders_out, place_order, refresh_order_status, resolve_lines)
from ..services.billing import quote_lines
from .common import get_or_404

router = APIRouter(tags=["orders"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ preparation areas

@router.get("/kitchen/prep-areas", response_model=list[PrepAreaOut])
def list_prep_areas(auth: Auth = Depends(require(Perm.kds_view)), db: Session = Depends(get_db)):
    return list(db.scalars(select(PrepArea).order_by(PrepArea.sort_order, PrepArea.name)))


@router.post("/kitchen/prep-areas", response_model=PrepAreaOut, status_code=201)
def create_prep_area(body: PrepAreaIn, auth: Auth = Depends(require(Perm.menu_manage)), db: Session = Depends(get_db)):
    area = PrepArea(tenant_id=auth.tenant_id, **body.model_dump())
    db.add(area)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "prep_area", area.id, after=audit.snapshot(area))
    return area


@router.patch("/kitchen/prep-areas/{area_id}", response_model=PrepAreaOut)
def update_prep_area(area_id: uuid.UUID, body: PrepAreaIn, auth: Auth = Depends(require(Perm.menu_manage)),
                     db: Session = Depends(get_db)):
    area = get_or_404(db, PrepArea, area_id, "Preparation area")
    before = audit.snapshot(area)
    for k, v in body.model_dump().items():
        setattr(area, k, v)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "update", "prep_area", area.id, before, audit.snapshot(area))
    return area


# ------------------------------------------------------------------ orders

@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(body: OrderCreate, auth: Auth = Depends(require(Perm.kot_send)), db: Session = Depends(get_db)):
    if body.id is not None:
        seen = db.get(OrderRequest, body.id)
        if seen is not None:  # retried request from a flaky connection: don't send it twice
            out = order_out(db, db.get(Order, seen.order_id))
            out.merged_into_existing = seen.merged
            return out
    outlet = get_or_404(db, Outlet, body.outlet_id, "Outlet")
    ensure_outlet_access(db, auth, outlet.id)
    if not outlet.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This outlet is switched off")
    from .dayclose import is_closed
    if is_closed(db, outlet.id, business_date()):
        raise HTTPException(status.HTTP_409_CONFLICT, "Today has been closed for this outlet. A manager can reopen it under Day close.")
    try:
        car = normalize_car_number(body.car_number) if body.order_type == "takeaway" else None
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    if body.order_type == "takeaway" and outlet.car_number_required and not car:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter the car number for this takeaway order")
    if body.order_type == "dine_in" and not (body.table_label or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick a table for dine-in orders")

    order, merged = place_order(
        db, tenant_id=auth.tenant_id, outlet=outlet, order_type=body.order_type,
        table=(body.table_label or "").strip().upper() or None, car=car,
        customer_name=(body.customer_name or "").strip() or None, customer_phone=None,
        notes=(body.notes or "").strip() or None, lines=body.items, created_by=auth.user_id, order_id=body.id,
    )
    if body.id is not None:
        db.add(OrderRequest(id=body.id, tenant_id=auth.tenant_id, order_id=order.id, merged=merged))
        db.flush()
    out = order_out(db, order)
    out.merged_into_existing = merged
    audit.record(db, auth.tenant_id, auth.user_id, "add_items" if merged else "create", "order", order.id,
                 after={"token": order.token_no, "kots": [k.kot_no for k in out.kots], "total_paise": out.total_paise})
    return out


@router.post("/orders/quote")
def quote_cart(body: QuoteIn, auth: Auth = Depends(require(Perm.kot_send)), db: Session = Depends(get_db)) -> dict:
    """Cart total with GST before the order is sent, for the counter's pay buttons."""
    outlet = get_or_404(db, Outlet, body.outlet_id, "Outlet")
    ensure_outlet_access(db, auth, outlet.id)
    try:
        calc = quote_lines(db, resolve_lines(db, outlet, body.items), body.discount_paise)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    return {"subtotal_paise": calc.gross_paise, "discount_paise": calc.discount_paise, "tax_paise": calc.tax_paise, "round_off_paise": calc.round_off_paise,
            "total_paise": calc.total_paise}


@router.post("/orders/{order_id}/items", response_model=OrderOut)
def add_items(order_id: uuid.UUID, body: AddItems, auth: Auth = Depends(require(Perm.kot_send)),
              db: Session = Depends(get_db)):
    """Items added later go out as new KOTs, so the kitchen only sees what's new."""
    order = get_or_404(db, Order, order_id, "Order")
    ensure_outlet_access(db, auth, order.outlet_id)
    if order.status in ("completed", "cancelled", "pending"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This order is {order.status}. Start a new order.")
    if db.scalar(select(Bill.id).where(Bill.order_id == order.id, Bill.status == "paid")) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This order is already billed. Start a new order for more items.")
    outlet = db.get(Outlet, order.outlet_id)
    kots = add_kots(db, order, resolve_lines(db, outlet, body.items))
    refresh_order_status(db, order)
    audit.record(db, auth.tenant_id, auth.user_id, "add_items", "order", order.id,
                 after={"kots": [k.kot_no for k in kots]})
    return order_out(db, order)


@router.get("/orders", response_model=list[OrderOut])
def list_orders(outlet_id: uuid.UUID, active: bool = True, car: str | None = None, token: int | None = None,
                auth: Auth = Depends(require(Perm.kot_send)), db: Session = Depends(get_db)):
    """Today's orders at an outlet. Search by car number (e.g. last 4 digits) or token."""
    ensure_outlet_access(db, auth, outlet_id)
    q = select(Order).where(Order.outlet_id == outlet_id, Order.business_date == business_date())
    if active:
        q = q.where(Order.status.notin_(("completed", "cancelled", "pending")))
    if token is not None:
        q = q.where(Order.token_no == token)
    orders = list(db.scalars(q.order_by(Order.token_no.desc()).limit(200)))
    if car:
        orders = [o for o in orders if car_matches(o.car_number, car)]
    return orders_out(db, orders)


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: uuid.UUID, auth: Auth = Depends(require(Perm.kds_view)), db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id, "Order")
    ensure_outlet_access(db, auth, order.outlet_id)
    return order_out(db, order)


@router.post("/orders/{order_id}/complete", response_model=OrderOut)
def complete_order(order_id: uuid.UUID, force: bool = False, auth: Auth = Depends(require(Perm.order_complete)),
                   db: Session = Depends(get_db)):
    """Served (dine-in) or handed over (takeaway/delivery). Marks every ticket served."""
    order = get_or_404(db, Order, order_id, "Order")
    ensure_outlet_access(db, auth, order.outlet_id)
    if order.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "This order was cancelled")
    kots = [k for k in db.scalars(select(Kot).where(Kot.order_id == order.id)) if k.status != "cancelled"]
    pending = [k for k in kots if k.status in ("new", "preparing")]
    if pending and not (force and auth.can(Perm.approve)):
        names = ", ".join(sorted({k.prep_area_name for k in pending}))
        raise HTTPException(status.HTTP_409_CONFLICT, f"Still being prepared at: {names}")
    for k in kots:
        if k.status != "served":
            k.status = "served"
            k.served_at = _now()
    refresh_order_status(db, order)
    audit.record(db, auth.tenant_id, auth.user_id, "complete", "order", order.id, after={"forced": bool(pending)})
    return order_out(db, order)


# ------------------------------------------------------------------ kitchen display

@router.get("/kds/outlets/{outlet_id}")
def kds_feed(outlet_id: uuid.UUID, prep_area_id: uuid.UUID | None = None,
             unassigned: bool = Query(default=False, description="Only tickets for items with no prep area"),
             auth: Auth = Depends(require(Perm.kds_view)), db: Session = Depends(get_db)) -> dict:
    """Open tickets for one outlet (optionally one preparation area), oldest first."""
    get_or_404(db, Outlet, outlet_id, "Outlet")
    ensure_outlet_access(db, auth, outlet_id)
    q = select(Kot).where(Kot.outlet_id == outlet_id, Kot.business_date == business_date(),
                          Kot.status.in_(("new", "preparing", "ready")))
    if prep_area_id:
        q = q.where(Kot.prep_area_id == prep_area_id)
    elif unassigned:
        q = q.where(Kot.prep_area_id.is_(None))
    kots = list(db.scalars(q.order_by(Kot.created_at, Kot.kot_no).limit(300)))
    orders = {o.id: o for o in db.scalars(select(Order).where(Order.id.in_({k.order_id for k in kots})))} if kots else {}
    now = _now()
    lines = lines_by_kot(db, [k.id for k in kots])
    return {
        "server_time": now.isoformat(),
        "kots": [kot_out(db, k, orders[k.order_id], now, items=lines.get(k.id, [])).model_dump(mode="json") for k in kots],
    }


@router.post("/kds/kots/{kot_id}/status", response_model=KotOut)
def change_kot_status(kot_id: uuid.UUID, body: StatusChange, auth: Auth = Depends(require(Perm.kds_view)),
                      db: Session = Depends(get_db)):
    kot = get_or_404(db, Kot, kot_id, "Ticket")
    ensure_outlet_access(db, auth, kot.outlet_id)
    if kot.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "This order was cancelled")
    needed = Perm.order_complete if body.status == "served" else Perm.kds_update
    if not auth.can(needed):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission for this")
    try:
        check_transition(kot.status, body.status)
    except TransitionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    before = kot.status
    kot.status = body.status
    now = _now()
    if body.status == "preparing" and kot.started_at is None:
        kot.started_at = now
    if body.status == "ready":
        kot.ready_at = now
        kot.started_at = kot.started_at or now
    if body.status == "served":
        kot.served_at = now
    if body.status in ("new", "preparing"):
        kot.ready_at = None
    if body.status == "new":
        kot.started_at = None
    order = db.get(Order, kot.order_id)
    was = order.status
    refresh_order_status(db, order)
    if order.status == "ready" and was != "ready":
        sms.on_order_ready(db, order)
    audit.record(db, auth.tenant_id, auth.user_id, "status", "kot", kot.id, {"status": before}, {"status": kot.status})
    db.flush()
    return kot_out(db, kot, order, now)


# ------------------------------------------------------------------ customer order-ready screen

READY_SHOW_MAX = 30


@router.get("/public/display/{display_key}", response_model=DisplayOut)
def customer_display(display_key: str) -> DisplayOut:
    """No login: the TV at the counter opens /display/<key>. Shows token numbers only."""
    with system_session() as db:
        outlet = db.scalar(select(Outlet).where(Outlet.display_key == display_key))
        if outlet is None or not outlet.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Display link not found")
        tenant = db.get(Tenant, outlet.tenant_id)
        orders = list(db.scalars(
            select(Order).where(Order.outlet_id == outlet.id, Order.business_date == business_date(),
                                Order.status.notin_(("completed", "cancelled", "pending")))
            .order_by(Order.token_no)
        ))

        def view(o: Order) -> DisplayOrder:
            return DisplayOrder(token_no=o.token_no, order_type=o.order_type, car_number=o.car_number,
                                table_label=o.table_label)

        ready = sorted((o for o in orders if o.status == "ready"), key=lambda o: o.ready_at or o.created_at,
                       reverse=True)[:READY_SHOW_MAX]
        return DisplayOut(
            outlet_name=outlet.name, app_name=tenant.app_name or tenant.name, logo_url=tenant.logo_url,
            primary_color=tenant.primary_color,
            show_powered_by=tenant.show_powered_by or tenant.plan != "chain",
            preparing=[view(o) for o in orders if o.status in ("new", "preparing")][:60],
            ready=[view(o) for o in ready], server_time=_now(),
        )


@router.post("/tenant/outlets/{outlet_id}/kitchen-key", response_model=OutletOut)
def rotate_kitchen_key(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.outlets_manage)),
                       db: Session = Depends(get_db)):
    """New link for the kitchen display (screens on the old link are signed out)."""
    outlet = get_or_404(db, Outlet, outlet_id, "Outlet")
    outlet.kitchen_key = secrets.token_urlsafe(16)
    for u in db.scalars(select(User).join(UserOutlet, UserOutlet.user_id == User.id)
                        .where(User.is_screen, UserOutlet.outlet_id == outlet.id)):
        u.is_active = False      # the old screen's token stops working at once
    audit.record(db, auth.tenant_id, auth.user_id, "rotate_kitchen_key", "outlet", outlet.id)
    return outlet


@router.post("/tenant/outlets/{outlet_id}/display-key", response_model=OutletOut)
def rotate_display_key(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.outlets_manage)),
                       db: Session = Depends(get_db)):
    """New link for the customer screen (the old link stops working)."""
    outlet = get_or_404(db, Outlet, outlet_id, "Outlet")
    outlet.display_key = secrets.token_urlsafe(16)
    audit.record(db, auth.tenant_id, auth.user_id, "rotate_display_key", "outlet", outlet.id)
    return outlet


# ------------------------------------------------------------------ split bill, move items, move table

class PickIn(BaseModel):
    order_item_id: uuid.UUID
    qty: int = Field(ge=1, le=999)


class SplitIn(BaseModel):
    items: list[PickIn] = Field(min_length=1, max_length=200)


class MoveIn(BaseModel):
    to_table: str = Field(min_length=1, max_length=20)
    items: list[PickIn] | None = None          # None = the whole order


def _open_for_change(db: Session, auth: Auth, order_id: uuid.UUID) -> Order:
    order = get_or_404(db, Order, order_id, "Order")
    ensure_outlet_access(db, auth, order.outlet_id)
    if order.status in ("completed", "cancelled", "pending") or order.merged_into_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This order is {order.status}")
    if db.scalar(select(Bill.id).where(Bill.order_id == order.id, Bill.status == "paid")) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This order is already billed")
    return order


def _picks(body_items: list[PickIn] | None) -> dict[uuid.UUID, int] | None:
    if body_items is None:
        return None
    out: dict[uuid.UUID, int] = {}
    for p in body_items:
        out[p.order_item_id] = out.get(p.order_item_id, 0) + p.qty
    return out


@router.post("/orders/{order_id}/split", response_model=OrderOut)
def split_order(order_id: uuid.UUID, body: SplitIn, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    """Split a bill by items: the chosen items become their own order (T4 -> T4/2), billed separately."""
    order = _open_for_change(db, auth, order_id)
    picks = _picks(body.items)
    assert picks is not None
    if order.table_label:
        lock_table(db, order.outlet_id, order.table_label.split("/")[0])
    label = next_split_label(db, order.outlet_id, order.table_label) if order.order_type == "dine_in" and order.table_label else None
    new = new_order_like(db, order, label, auth.user_id)
    moved = move_items(db, order, new, picks)
    if order.merged_into_id is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That's every item: nothing to split off")
    audit.record(db, auth.tenant_id, auth.user_id, "split", "order", order.id, after={"new_order": str(new.id), "token": new.token_no, "pieces": moved})
    return order_out(db, new)


@router.post("/orders/{order_id}/move", response_model=OrderOut)
def move_order(order_id: uuid.UUID, body: MoveIn, auth: Auth = Depends(require(Perm.kot_send)), db: Session = Depends(get_db)):
    """Move a whole table (T4 -> T7), or some items to another table. If that table already has an open
    order, they join it; otherwise the table gets a new order (or, for the whole order, just a new label)."""
    order = _open_for_change(db, auth, order_id)
    if order.order_type != "dine_in":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only dine-in orders have tables")
    to = body.to_table.strip()
    if to == order.table_label:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That's the same table")
    lock_table(db, order.outlet_id, to)
    target = open_table_order(db, order.outlet_id, to)
    before = order.table_label
    if target is None and body.items is None:
        order.table_label = to
        audit.record(db, auth.tenant_id, auth.user_id, "move_table", "order", order.id, after={"from": before, "to": to})
        return order_out(db, order)
    if target is None:
        target = new_order_like(db, order, to, auth.user_id)
    moved = move_items(db, order, target, _picks(body.items))
    audit.record(db, auth.tenant_id, auth.user_id, "move_items", "order", order.id,
                 after={"from": before, "to": to, "into_order": str(target.id), "pieces": moved, "all": body.items is None})
    return order_out(db, target)
