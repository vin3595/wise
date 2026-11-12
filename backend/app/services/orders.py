"""Create orders, split them into KOTs per preparation area, and keep order status in sync."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..core.kds import AreaKey, business_date, order_status, split_by_area, ticket_age_level
from ..core.menu_resolve import Override, resolve_variant
from ..models import (
    Addon, Category, DailyCounter, Item, ItemAddonGroup, Kot, Order, OrderItem, Outlet, OutletVariantOverride,
    PrepArea, Variant,
)
from ..schemas import KotLineOut, KotOut, OrderLineIn, OrderOut


def _now() -> datetime:
    return datetime.now(timezone.utc)


def next_number(db: Session, tenant_id: uuid.UUID, outlet_id: uuid.UUID, day: date, kind: str) -> int:
    """Atomic per-outlet, per-day counter (safe with several counters billing at once)."""
    stmt = (
        insert(DailyCounter)
        .values(outlet_id=outlet_id, business_date=day, kind=kind, tenant_id=tenant_id, last_no=1)
        .on_conflict_do_update(
            index_elements=[DailyCounter.outlet_id, DailyCounter.business_date, DailyCounter.kind],
            set_={"last_no": DailyCounter.last_no + 1},
        )
        .returning(DailyCounter.last_no)
    )
    return int(db.execute(stmt).scalar_one())


@dataclass
class ResolvedLine:
    line: OrderLineIn
    variant: Variant
    item: Item
    price_paise: int
    addons: list[dict]
    area: AreaKey | None


def resolve_lines(db: Session, outlet: Outlet, lines: list[OrderLineIn]) -> list[ResolvedLine]:
    """Validate every line against this outlet's menu (availability, outlet price, allowed add-ons)."""
    areas = {a.id: a for a in db.scalars(select(PrepArea))}
    out: list[ResolvedLine] = []
    for line in lines:
        variant = db.get(Variant, line.variant_id)
        item = db.get(Item, variant.item_id) if variant else None
        if variant is None or item is None or item.brand_id != outlet.brand_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "An item isn't on this outlet's menu")
        cat = db.get(Category, item.category_id)
        ov_row = db.scalar(select(OutletVariantOverride).where(
            OutletVariantOverride.outlet_id == outlet.id, OutletVariantOverride.variant_id == variant.id))
        ov = Override(ov_row.is_active, ov_row.price_paise) if ov_row and (ov_row.is_active is not None or ov_row.price_paise is not None) else None
        available, price = resolve_variant(variant.is_active, variant.price_paise, item.is_active,
                                           bool(cat and cat.is_active), ov)
        if not available:
            raise HTTPException(status.HTTP_409_CONFLICT, f"{item.name} ({variant.name}) isn't available right now")
        if ov_row is not None and ov_row.sold_out_on == business_date():
            raise HTTPException(status.HTTP_409_CONFLICT, f"{item.name} ({variant.name}) is sold out today")

        addons: list[dict] = []
        if line.addon_ids:
            allowed_groups = set(db.scalars(select(ItemAddonGroup.group_id).where(ItemAddonGroup.item_id == item.id)))
            for aid in dict.fromkeys(line.addon_ids):
                addon = db.get(Addon, aid)
                if addon is None or not addon.is_active or addon.group_id not in allowed_groups:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Add-on not allowed on {item.name}")
                addons.append({"id": str(addon.id), "name": addon.name, "price_paise": addon.price_paise})
        area = None
        if item.prep_area_id and item.prep_area_id in areas and areas[item.prep_area_id].is_active:
            a = areas[item.prep_area_id]
            area = AreaKey(str(a.id), a.name)
        out.append(ResolvedLine(line, variant, item, price, addons, area))
    return out


def add_kots(db: Session, order: Order, lines: list[ResolvedLine]) -> list[Kot]:
    """One KOT per preparation area for these lines. Each call is a new 'round' (KOT #2 for added items)."""
    last_round = db.scalar(select(func.max(Kot.round_no)).where(Kot.order_id == order.id)) or 0
    kots = []
    for area, group in split_by_area(lines, lambda r: r.area):
        kot = Kot(
            tenant_id=order.tenant_id, order_id=order.id, outlet_id=order.outlet_id,
            prep_area_id=uuid.UUID(area.id) if area.id else None, prep_area_name=area.name,
            business_date=order.business_date, round_no=last_round + 1,
            kot_no=next_number(db, order.tenant_id, order.outlet_id, order.business_date, "kot"),
        )
        db.add(kot)
        db.flush()
        for r in group:
            db.add(OrderItem(
                tenant_id=order.tenant_id, order_id=order.id, kot_id=kot.id, variant_id=r.variant.id,
                item_name=r.item.name, variant_name=r.variant.name, food_type=r.item.food_type,
                unit_price_paise=r.price_paise, qty=r.line.qty, addons=r.addons or None,
                notes=(r.line.notes or "").strip() or None,
            ))
        kots.append(kot)
    db.flush()
    return kots


def refresh_order_status(db: Session, order: Order) -> None:
    if order.status == "cancelled":
        return
    statuses = [s for s in db.scalars(select(Kot.status).where(Kot.order_id == order.id)) if s != "cancelled"]
    new_status = order_status(statuses)
    if new_status == order.status:
        return
    order.status = new_status
    if new_status == "ready" and order.ready_at is None:
        order.ready_at = _now()
    if new_status == "completed":
        order.completed_at = _now()
    if new_status in ("new", "preparing"):
        order.ready_at = None


def lines_by_kot(db: Session, kot_ids) -> dict:
    """Every ticket's lines in ONE query (a screen with 40 tickets used to make 40)."""
    out: dict = {}
    if not kot_ids:
        return out
    for i in db.scalars(select(OrderItem).where(OrderItem.kot_id.in_(list(kot_ids))).order_by(OrderItem.created_at)):
        out.setdefault(i.kot_id, []).append(i)
    return out


def kot_out(db: Session, kot: Kot, order: Order, now: datetime | None = None, items: list | None = None) -> KotOut:
    if items is None:
        items = list(db.scalars(select(OrderItem).where(OrderItem.kot_id == kot.id).order_by(OrderItem.created_at)))
    lines = [
        KotLineOut(id=i.id, item_name=i.item_name, variant_name=i.variant_name, food_type=i.food_type, qty=i.qty,
                   addons=i.addons or [], notes=i.notes)
        for i in items
    ]
    return KotOut(
        id=kot.id, order_id=order.id, kot_no=kot.kot_no, prep_area_id=kot.prep_area_id,
        prep_area_name=kot.prep_area_name, status=kot.status, created_at=kot.created_at,
        started_at=kot.started_at, ready_at=kot.ready_at,
        age_level=ticket_age_level(kot.created_at, now or _now()) if kot.status in ("new", "preparing") else "ok",
        token_no=order.token_no or 0, order_type=order.order_type, table_label=order.table_label,
        car_number=order.car_number, customer_name=order.customer_name, order_notes=order.notes,
        is_addon_kot=kot.round_no > 1, printed=kot.printed_at is not None, lines=lines,
    )


def orders_out(db: Session, orders: list[Order]) -> list[OrderOut]:
    """Many orders in 3 queries in all (the open-orders list is read every few seconds at every counter)."""
    if not orders:
        return []
    from ..models import Bill
    ids = [o.id for o in orders]
    kots: dict = {}
    for k in db.scalars(select(Kot).where(Kot.order_id.in_(ids)).order_by(Kot.kot_no)):
        kots.setdefault(k.order_id, []).append(k)
    items: dict = {}
    for i in db.scalars(select(OrderItem).where(OrderItem.order_id.in_(ids)).order_by(OrderItem.created_at)):
        items.setdefault(i.order_id, []).append(i)
    bills = {b.order_id: b for b in db.scalars(select(Bill).where(Bill.order_id.in_(ids), Bill.status == "paid"))}
    return [order_out(db, o, _pre=(kots.get(o.id, []), items.get(o.id, []), bills.get(o.id))) for o in orders]


def order_out(db: Session, order: Order, _pre: tuple | None = None) -> OrderOut:
    from ..models import Bill  # local import keeps the module order simple
    if _pre is None:
        kots = list(db.scalars(select(Kot).where(Kot.order_id == order.id).order_by(Kot.kot_no)))
        all_items = list(db.scalars(select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.created_at)))
        bill = db.scalar(select(Bill).where(Bill.order_id == order.id, Bill.status == "paid"))
    else:
        kots, all_items, bill = _pre
    total = 0
    by_kot: dict = {}
    for i in all_items:
        total += i.qty * (i.unit_price_paise + sum(a["price_paise"] for a in (i.addons or [])))
        by_kot.setdefault(i.kot_id, []).append(i)
    pending = [
        {"item": p.get("item_name"), "variant": p.get("variant_name"), "qty": p["qty"],
         "addons": p.get("addon_names", []), "notes": p.get("notes")}
        for p in (order.pending_items or [])
    ]
    if order.status == "pending":
        total = sum(p["qty"] * p.get("unit_paise", 0) for p in (order.pending_items or []))
    return OrderOut(
        bill_id=bill.id if bill else None, invoice_no=bill.invoice_no if bill else None,
        cancel_reason=order.cancel_reason, source=order.source, customer_phone=order.customer_phone,
        pending_lines=pending, payment_status=order.payment_status, pay_method=order.pay_method,
        quoted_total_paise=order.quoted_total_paise, pay_ref=order.pay_ref,
        id=order.id, outlet_id=order.outlet_id, business_date=order.business_date, token_no=order.token_no,
        order_type=order.order_type, table_label=order.table_label, car_number=order.car_number,
        customer_name=order.customer_name, notes=order.notes, status=order.status, created_at=order.created_at,
        ready_at=order.ready_at, completed_at=order.completed_at,
        kots=[kot_out(db, k, order, items=by_kot.get(k.id, [])) for k in kots], total_paise=total,
    )


def lock_table(db: Session, outlet_id: uuid.UUID, table: str) -> None:
    """Serialise orders for one table: if two waiters punch T4 at the same moment, the second waits
    for the first to commit and then joins its order instead of opening a duplicate."""
    db.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"table:{outlet_id}:{table}"})


def open_table_order(db: Session, outlet_id: uuid.UUID, table: str) -> Order | None:
    from ..models import Bill
    for o in db.scalars(select(Order).where(
            Order.outlet_id == outlet_id, Order.table_label == table, Order.order_type == "dine_in",
            Order.status.in_(("new", "preparing", "ready")), Order.business_date == business_date())
            .order_by(Order.created_at.desc())):
        if db.scalar(select(Bill.id).where(Bill.order_id == o.id, Bill.status == "paid")) is None:
            return o
    return None


def assign_token(db: Session, order: Order) -> None:
    if order.token_no is None:
        order.token_no = next_number(db, order.tenant_id, order.outlet_id, order.business_date, "token")


def place_order(db: Session, *, tenant_id: uuid.UUID, outlet: Outlet, order_type: str, table: str | None,
                car: str | None, customer_name: str | None, customer_phone: str | None, notes: str | None,
                lines: list[OrderLineIn], created_by: uuid.UUID | None, order_id: uuid.UUID | None = None,
                source: str = "staff") -> tuple[Order, bool]:
    """Creates the order and its KOTs. Dine-in items for a table that already has an open,
    unbilled order are added to that order as a new KOT round. Returns (order, merged)."""
    resolved = resolve_lines(db, outlet, lines)
    if order_type == "dine_in" and table:
        lock_table(db, outlet.id, table)
        existing = open_table_order(db, outlet.id, table)
        if existing is not None:
            add_kots(db, existing, resolved)
            refresh_order_status(db, existing)
            return existing, True
    order = Order(
        id=order_id if order_id and db.get(Order, order_id) is None else uuid.uuid4(), tenant_id=tenant_id, outlet_id=outlet.id, business_date=business_date(),
        order_type=order_type, table_label=table if order_type == "dine_in" else None, car_number=car,
        customer_name=customer_name, customer_phone=customer_phone, notes=notes, created_by=created_by,
        source=source,
    )
    db.add(order)
    db.flush()
    assign_token(db, order)
    add_kots(db, order, resolved)
    return order, False


# ------------------------------------------------------------------ split a bill / move items or a table

def _clone_kot(db: Session, src: Kot, target: Order) -> Kot:
    """Part of a ticket moving to another order: same kitchen state, already printed (no second slip)."""
    last_round = db.scalar(select(func.max(Kot.round_no)).where(Kot.order_id == target.id)) or 0
    k = Kot(tenant_id=src.tenant_id, order_id=target.id, outlet_id=src.outlet_id, prep_area_id=src.prep_area_id,
            prep_area_name=src.prep_area_name, business_date=src.business_date, round_no=max(last_round, 1),
            kot_no=next_number(db, src.tenant_id, src.outlet_id, src.business_date, "kot"), status=src.status,
            started_at=src.started_at, ready_at=src.ready_at, served_at=src.served_at,
            printed_at=src.printed_at or _now(), print_count=max(src.print_count, 1))
    db.add(k)
    db.flush()
    return k


def move_items(db: Session, src: Order, target: Order, picks: dict[uuid.UUID, int] | None) -> int:
    """Moves items (item id -> how many; None = everything) from one open order to another, with their
    kitchen tickets. Returns how many pieces moved. The source closes if nothing is left on it."""
    kots = {k.id: k for k in db.scalars(select(Kot).where(Kot.order_id == src.id))}
    live = [i for i in db.scalars(select(OrderItem).where(OrderItem.order_id == src.id).order_by(OrderItem.created_at))
            if kots.get(i.kot_id) is not None and kots[i.kot_id].status != "cancelled"]
    if picks is None:
        picks = {i.id: i.qty for i in live}
    by_id = {i.id: i for i in live}
    for iid, q in picks.items():
        if iid not in by_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "An item isn't on this order any more")
        if not 0 < q <= by_id[iid].qty:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Only {by_id[iid].qty} × {by_id[iid].item_name} on this order")
    moved = 0
    for kid, kot in kots.items():
        mine = [i for i in live if i.kot_id == kid]
        chosen = [i for i in mine if i.id in picks]
        if not chosen:
            continue
        whole = all(picks.get(i.id) == i.qty for i in mine)
        if whole:                                   # the whole ticket goes
            kot.order_id = target.id
            for i in mine:
                i.order_id = target.id
                moved += i.qty
            continue
        clone = _clone_kot(db, kot, target)
        for i in chosen:
            q = picks[i.id]
            if q == i.qty:
                i.order_id, i.kot_id = target.id, clone.id
            else:
                i.qty -= q
                db.add(OrderItem(tenant_id=i.tenant_id, order_id=target.id, kot_id=clone.id, variant_id=i.variant_id,
                                 item_name=i.item_name, variant_name=i.variant_name, food_type=i.food_type,
                                 unit_price_paise=i.unit_price_paise, qty=q, addons=i.addons, notes=i.notes))
            moved += q
    db.flush()
    left = [i for i in db.scalars(select(OrderItem).where(OrderItem.order_id == src.id))
            if (k := db.get(Kot, i.kot_id)) is not None and k.status != "cancelled"]
    if not left:
        src.status, src.completed_at, src.merged_into_id = "completed", _now(), target.id
    else:
        refresh_order_status(db, src)
    refresh_order_status(db, target)
    return moved


def new_order_like(db: Session, src: Order, table: str | None, created_by: uuid.UUID | None) -> Order:
    """An empty order at the same outlet (a split-off bill, or a table that was free)."""
    o = Order(tenant_id=src.tenant_id, outlet_id=src.outlet_id, business_date=src.business_date, order_type=src.order_type,
              table_label=table, car_number=src.car_number if table is None else None, customer_name=None,
              customer_phone=None, notes=None, created_by=created_by, source="staff", status=src.status)
    db.add(o)
    db.flush()
    assign_token(db, o)
    return o


def next_split_label(db: Session, outlet_id: uuid.UUID, table: str) -> str:
    """T4 -> T4/2, T4/3 ... (a free label, so new items for T4 still go to T4's own order)."""
    base = table.split("/")[0]
    n = 2
    while open_table_order(db, outlet_id, f"{base}/{n}") is not None:
        n += 1
    return f"{base}/{n}"[:20]
