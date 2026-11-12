"""Billing on the counter while the box can't be reached.

1. While online, a billing counter registers once (POST /offline/devices) and gets its own invoice
   series, a block of token numbers, and an "offline pack": GST rate and HSN per item, and the outlet's
   bill header. It refreshes the pack every time the screen loads.
2. Offline, the counter bills from its saved menu with the same GST maths as the box (frontend/src/gst.ts,
   checked against core/tax.py on 3000 random bills), prints the bill and the KOT slips itself, and queues
   the bill on the device.
3. Back online, it sends each queued bill here. A bill is accepted once only (by the id the device gave it),
   so a retry after a dropped connection never makes a second bill.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.billing import LineIn, PaymentIn, compute_bill, fiscal_year_start, invoice_number, validate_payments
from ..core.kds import AreaKey, business_date, split_by_area
from ..core.menu_resolve import Override, resolve_variant
from ..core.offline import Seen, differences, series_for, token_base
from ..core.permissions import Perm
from ..core.tax import TaxGroup as TaxCalc
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..models import (
    Addon, Bill, Category, CounterDevice, Item, Kot, OfflineSync, Order, OrderItem, OrderRequest, Outlet,
    OutletVariantOverride,
    PrepArea, Shift, TaxGroup, Tenant, User, Variant,
)
from ..schemas import BillOut, CustomerIn
from ..services import audit, sms
from ..services.billing import Row, bill_out, save_bill
from ..services.orders import next_number
from .common import get_or_404

router = APIRouter(prefix="/offline", tags=["offline"])


class DeviceIn(BaseModel):
    id: uuid.UUID
    outlet_id: uuid.UUID
    name: str = Field(default="Counter", min_length=1, max_length=40)


class TaxIn(BaseModel):
    cgst_bp: int = Field(ge=0, le=5000)
    sgst_bp: int = Field(ge=0, le=5000)
    igst_bp: int = Field(ge=0, le=5000)
    inclusive: bool = False


class AddonIn(BaseModel):
    id: uuid.UUID
    name: str = Field(max_length=80)
    price_paise: int = Field(ge=0)


class OfflineLineIn(BaseModel):
    variant_id: uuid.UUID
    qty: int = Field(ge=1, le=999)
    price_paise: int = Field(ge=0)          # variant price as the counter had it
    addons: list[AddonIn] = []
    notes: str | None = Field(default=None, max_length=120)
    tax: TaxIn


class OfflinePayIn(BaseModel):
    mode: str = Field(pattern=r"^(cash|upi|card)$")   # no credit or complimentary bills offline
    amount_paise: int = Field(ge=0)


class OfflineBillIn(BaseModel):
    id: uuid.UUID                     # the device's id for this bill
    device_id: uuid.UUID
    made_at: datetime
    made_by: uuid.UUID
    fy_start: int
    seq: int = Field(ge=1)
    invoice_no: str = Field(max_length=16)
    token_no: int
    order_type: str = Field(pattern=r"^(dine_in|takeaway|delivery)$")
    table_label: str | None = Field(default=None, max_length=20)
    car_number: str | None = Field(default=None, max_length=10)
    customer_name: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=200)
    customer: CustomerIn = CustomerIn()
    lines: list[OfflineLineIn] = Field(min_length=1, max_length=100)
    payments: list[OfflinePayIn] = Field(min_length=1, max_length=3)
    total_paise: int                  # as printed on the bill
    # The order id the counter tried to send just as the connection dropped. If that order did reach
    # the box, it is closed here so the same food isn't billed or made twice.
    tried_order_id: uuid.UUID | None = None


class SyncOut(BaseModel):
    bill: BillOut
    notes: list[str]
    duplicate: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pack(db: Session, device: CounterDevice) -> dict:
    outlet = db.get(Outlet, device.outlet_id)
    tenant = db.get(Tenant, device.tenant_id)
    fy = fiscal_year_start(business_date(), tenant.fy_start_month)
    last = db.scalar(select(func.max(Bill.seq)).where(Bill.outlet_id == outlet.id, Bill.series == device.series,
                                                      Bill.fy_start == fy)) or 0
    taxes = {t.id: t for t in db.scalars(select(TaxGroup))}
    items = {}
    for it in db.scalars(select(Item).where(Item.brand_id == outlet.brand_id, Item.deleted_at.is_(None))):
        tg = taxes.get(it.tax_group_id) if it.tax_group_id else None
        items[str(it.id)] = {"cgst_bp": tg.cgst_bp if tg else 0, "sgst_bp": tg.sgst_bp if tg else 0,
                             "igst_bp": tg.igst_bp if tg else 0, "inclusive": bool(tg and tg.is_inclusive),
                             "hsn_sac": it.hsn_sac}
    areas = {str(a.id): a.name for a in db.scalars(select(PrepArea).where(PrepArea.is_active.is_(True)))}
    return {
        "device_id": str(device.id), "name": device.name, "letter": device.letter, "series": device.series,
        "token_base": token_base(device.letter), "fy_start": fy, "fy_start_month": tenant.fy_start_month,
        "last_seq": last, "items": items, "prep_areas": areas,
        "outlet": {"name": outlet.name, "legal_name": tenant.name, "address": outlet.address,
                   "gstin": outlet.gstin or tenant.gstin, "fssai": outlet.fssai, "phone": outlet.phone,
                   "footer": outlet.bill_footer, "logo_url": tenant.logo_url, "brand": tenant.app_name or tenant.name},
    }


@router.post("/devices")
def register_device(body: DeviceIn, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> dict:
    """Register this counter for offline billing (safe to call every time the billing screen opens)."""
    outlet = get_or_404(db, Outlet, body.outlet_id, "Outlet")
    ensure_outlet_access(db, auth, outlet.id)
    device = db.get(CounterDevice, body.id)
    if device is not None:
        if device.outlet_id != outlet.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This counter is registered to another outlet")
        return _pack(db, device)
    # One registration at a time per outlet, so two counters can't take the same letter.
    db.execute(select(Outlet.id).where(Outlet.id == outlet.id).with_for_update())
    taken = set(db.scalars(select(CounterDevice.letter).where(CounterDevice.outlet_id == outlet.id)))
    try:
        letter, series = series_for(outlet.code, taken)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    device = CounterDevice(id=body.id, tenant_id=auth.tenant_id, outlet_id=outlet.id, letter=letter, series=series,
                           name=body.name.strip(), registered_by=auth.user_id)
    db.add(device)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "counter_device", device.id,
                 after={"series": series, "name": device.name})
    return _pack(db, device)


@router.get("/devices")
def list_devices(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)) -> list[dict]:
    ensure_outlet_access(db, auth, outlet_id)
    out = []
    for d in db.scalars(select(CounterDevice).where(CounterDevice.outlet_id == outlet_id).order_by(CounterDevice.letter)):
        n = db.scalar(select(func.count()).select_from(OfflineSync).where(OfflineSync.device_id == d.id)) or 0
        out.append({"id": str(d.id), "name": d.name, "series": d.series, "offline_bills": n,
                    "last_sync_at": d.last_sync_at.isoformat() if d.last_sync_at else None})
    return out


def _menu_now(db: Session, outlet: Outlet, variant: Variant, item: Item) -> int | None:
    cat = db.get(Category, item.category_id)
    ov_row = db.scalar(select(OutletVariantOverride).where(
        OutletVariantOverride.outlet_id == outlet.id, OutletVariantOverride.variant_id == variant.id))
    ov = Override(ov_row.is_active, ov_row.price_paise) if ov_row else None
    available, price = resolve_variant(variant.is_active, variant.price_paise, item.is_active,
                                       bool(cat and cat.is_active), ov)
    return price if available else None


@router.post("/bills", response_model=SyncOut)
def sync_bill(body: OfflineBillIn, auth: Auth = Depends(require(Perm.billing)), db: Session = Depends(get_db)):
    """Add one bill made while the counter was offline. The bill keeps its offline number and time."""
    seen = db.get(OfflineSync, body.id)
    if seen is not None:
        return SyncOut(bill=bill_out(db, db.get(Bill, seen.bill_id)), notes=seen.notes or [], duplicate=True)
    device = get_or_404(db, CounterDevice, body.device_id, "Counter")
    ensure_outlet_access(db, auth, device.outlet_id)
    outlet = db.get(Outlet, device.outlet_id)
    tenant = db.get(Tenant, auth.tenant_id)
    cashier = db.get(User, body.made_by)
    if cashier is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The cashier on this bill isn't a user here")

    day = business_date(body.made_at)
    fy = fiscal_year_start(day, tenant.fy_start_month)
    if fy != body.fy_start or invoice_number(device.series, fy, body.seq) != body.invoice_no:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Bill {body.invoice_no} doesn't belong to this counter's series {device.series}")
    if tenant.bills_locked_through and day <= tenant.bills_locked_through:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Bills up to {tenant.bills_locked_through} are locked. "
                            "The owner can move the lock date under Bills to add this offline bill.")
    if db.scalar(select(Bill.id).where(Bill.outlet_id == outlet.id, Bill.invoice_no == body.invoice_no)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Bill {body.invoice_no} is already on the box")
    shift = db.scalar(select(Shift).where(Shift.outlet_id == outlet.id, Shift.opened_by == cashier.id,
                                          Shift.status == "open"))
    if shift is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{cashier.name} needs an open shift here to add their offline bills")
    c = body.customer
    if not c.phone and not c.declined:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The bill has no mobile number and no 'declined'")

    # Lines exactly as billed; the box's menu today is only compared, for the manager's notes.
    areas = {a.id: a for a in db.scalars(select(PrepArea))}
    resolved: list[tuple] = []
    seen_lines: list[Seen] = []
    rows_in: list[LineIn] = []
    for ln in body.lines:
        variant = db.get(Variant, ln.variant_id)
        item = db.get(Item, variant.item_id) if variant else None
        if variant is None or item is None or item.brand_id != outlet.brand_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "An item on this bill isn't on this outlet's menu")
        for a in ln.addons:
            if db.get(Addon, a.id) is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"An add-on on {item.name} no longer exists")
        tax = TaxCalc(ln.tax.cgst_bp, ln.tax.sgst_bp, ln.tax.igst_bp, ln.tax.inclusive)
        tg = db.get(TaxGroup, item.tax_group_id) if item.tax_group_id else None
        addons_paise = sum(a.price_paise for a in ln.addons)
        now_price = _menu_now(db, outlet, variant, item)
        name = f"{item.name} ({variant.name})"
        seen_lines.append(Seen(name, ln.price_paise + addons_paise,
                               None if now_price is None else now_price + addons_paise,
                               tax.cgst_bp + tax.sgst_bp + tax.igst_bp,
                               (tg.cgst_bp + tg.sgst_bp + tg.igst_bp) if tg else 0))
        area = areas.get(item.prep_area_id) if item.prep_area_id else None
        resolved.append((ln, variant, item, AreaKey(str(area.id), area.name) if area else None))
        rows_in.append(LineIn(str(len(rows_in)), ln.qty, ln.price_paise + addons_paise, tax))

    calc = compute_bill(rows_in, None)
    if calc.total_paise != body.total_paise:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Bill {body.invoice_no}: printed total ₹{body.total_paise / 100:g} but the lines add up "
                            f"to ₹{calc.total_paise / 100:g}. Keep the printed copy and tell the manager.")
    try:
        validate_payments(calc.total_paise, [PaymentIn(p.mode, p.amount_paise) for p in body.payments])
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None

    token = body.token_no
    taken = db.scalar(select(Order.id).where(Order.outlet_id == outlet.id, Order.business_date == day,
                                             Order.token_no == token))
    notes = differences(seen_lines)
    from .dayclose import is_closed
    if is_closed(db, outlet.id, day):
        notes.append(f"Added after {day:%d %b} was closed: the day's close figures don't include it")
    if taken is not None:
        token = next_number(db, auth.tenant_id, outlet.id, day, "token")
        notes.append(f"Token #{body.token_no} was already used that day, so the order is filed as #{token}")
    order = Order(tenant_id=auth.tenant_id, outlet_id=outlet.id, business_date=day, token_no=token,
                  order_type=body.order_type, source="offline",
                  table_label=(body.table_label or "").strip().upper() or None if body.order_type == "dine_in" else None,
                  car_number=body.car_number, customer_name=(body.customer_name or "").strip() or None,
                  customer_phone=c.phone, notes=body.notes, created_by=cashier.id, status="completed",
                  completed_at=body.made_at)
    db.add(order)
    db.flush()
    # KOTs were printed on the counter's own printer; file them as printed and served, so the print bridge
    # and the kitchen screen don't pick them up again.
    items_by_index: dict[int, OrderItem] = {}
    indexed = list(enumerate(resolved))
    for area, group in split_by_area(indexed, lambda r: r[1][3]):
        kot = Kot(tenant_id=auth.tenant_id, order_id=order.id, outlet_id=outlet.id,
                  prep_area_id=uuid.UUID(area.id) if area.id else None, prep_area_name=area.name,
                  business_date=day, round_no=1, status="served", served_at=body.made_at,
                  printed_at=body.made_at, print_count=1,
                  kot_no=next_number(db, auth.tenant_id, outlet.id, day, "kot"))
        db.add(kot)
        db.flush()
        for i, (ln, variant, item, _a) in group:
            oi = OrderItem(tenant_id=auth.tenant_id, order_id=order.id, kot_id=kot.id, variant_id=variant.id,
                           item_name=item.name, variant_name=variant.name, food_type=item.food_type,
                           unit_price_paise=ln.price_paise, qty=ln.qty,
                           addons=[{"id": str(a.id), "name": a.name, "price_paise": a.price_paise} for a in ln.addons] or None,
                           notes=(ln.notes or "").strip() or None)
            db.add(oi)
            items_by_index[i] = oi
    db.flush()
    rows = [Row(items_by_index[i], resolved[i][2].hsn_sac, rows_in[i]) for i in range(len(resolved))]

    bill = Bill(
        id=body.id, tenant_id=auth.tenant_id, outlet_id=outlet.id, order_id=order.id, shift_id=shift.id,
        business_date=day, fy_start=fy, series=device.series, seq=body.seq, invoice_no=body.invoice_no,
        gross_paise=calc.gross_paise, discount_paise=0, taxable_paise=calc.taxable_paise,
        cgst_paise=calc.cgst_paise, sgst_paise=calc.sgst_paise, igst_paise=calc.igst_paise,
        round_off_paise=calc.round_off_paise, total_paise=calc.total_paise,
        customer_name=(c.name or body.customer_name or "").strip() or None, customer_phone=c.phone,
        customer_declined_phone=c.declined, customer_gstin=c.gstin, created_by=cashier.id, created_at=body.made_at,
    )
    save_bill(db, bill, rows, calc, [(p.mode, p.amount_paise, None) for p in body.payments])
    db.add(OfflineSync(id=body.id, tenant_id=auth.tenant_id, device_id=device.id, bill_id=bill.id,
                       notes=notes or None, made_at=body.made_at))
    device.last_sync_at = _now()
    if body.tried_order_id and (req := db.get(OrderRequest, body.tried_order_id)) is not None:
        half = db.get(Order, req.order_id)
        billed = db.scalar(select(Bill.id).where(Bill.order_id == half.id, Bill.status == "paid")) if half else None
        if half is not None and half.status != "cancelled" and billed is None:
            for k in db.scalars(select(Kot).where(Kot.order_id == half.id)):
                k.status = "cancelled"
                k.cancel_printed_at = _now()   # no cancel slip: the food was made once, for the offline bill
            half.status = "cancelled"
            half.cancelled_at = _now()
            half.cancelled_by = auth.user_id
            half.cancel_reason = f"Billed offline as {bill.invoice_no}"
            notes.append(f"Token #{half.token_no} had reached the kitchen before the connection dropped; "
                         f"it is closed as billed offline on {bill.invoice_no}")
            sync_row = db.get(OfflineSync, body.id)
            if sync_row is not None:
                sync_row.notes = notes
    sms.on_bill_created(db, bill)
    audit.record(db, auth.tenant_id, auth.user_id, "create_offline", "bill", bill.id,
                 after={"invoice_no": bill.invoice_no, "total_paise": bill.total_paise, "made_at": body.made_at.isoformat(),
                        "made_by": str(cashier.id), "device": device.name, "notes": notes})
    db.flush()
    return SyncOut(bill=bill_out(db, bill), notes=notes)
