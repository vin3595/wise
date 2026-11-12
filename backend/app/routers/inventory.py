"""Inventory: stock items, suppliers, recipes, purchases, in-house prep, wastage, closing counts,
stock on hand, reorder list and per-item costing.

Who sees what: anyone with stock.count can enter a closing count (blind: they don't see expected stock).
Purchases, recipes, prep, wastage and stock items are inventory.manage (owner, manager). Costs, values
and margins need costing.view, so staff never see them.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.kds import business_date
from ..core.permissions import Perm
from ..core.stock import (BASE_UNITS, STANDARD_UNITS, M, finds_loop, margin_bp, recipe_cost, reorder_qty, rolled_cost_k, scale,
                          unit_cost_k, value_paise)
from ..deps import Auth, ensure_outlet_access, get_db, require
from ..models import (
    Addon, AddonGroup, Item, Outlet, OutletVariantOverride, Purchase, RecipeLine, StockCount, StockItem, StockLevel,
    StockMove, Supplier, TaxGroup, Variant,
)
from ..services import audit
from ..services import stock as st
from .common import get_or_404

router = APIRouter(prefix="/inventory", tags=["inventory"])


# ------------------------------------------------------------------ schemas

class UnitIn(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    factor: int = Field(ge=1, le=1_000_000)      # base units in one of these


class StockItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(default="raw", pattern=r"^(raw|prep)$")
    unit: str = Field(pattern=r"^(g|ml|pc)$")
    category: str | None = Field(default=None, max_length=40)
    purchase_units: list[UnitIn] = []
    default_supplier_id: uuid.UUID | None = None
    min_level_m: int = Field(default=0, ge=0)
    reorder_qty_m: int = Field(default=0, ge=0)
    yield_m: int | None = Field(default=None, gt=0)
    made_on_sale: bool = False
    is_active: bool = True


class SupplierIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=15)
    gstin: str | None = Field(default=None, max_length=15)
    is_active: bool = True


class RecipeLineIn(BaseModel):
    stock_item_id: uuid.UUID
    qty_m: int = Field(gt=0, le=10**12)


class RecipeIn(BaseModel):
    lines: list[RecipeLineIn] = Field(max_length=60)


class CopyIn(BaseModel):
    from_variant_id: uuid.UUID
    to_variant_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    factor_bp: int = Field(default=10000, ge=100, le=1_000_000)   # 10000 = same quantities


class PurchaseLineIn(BaseModel):
    stock_item_id: uuid.UUID
    qty_m: int = Field(gt=0)                 # in the chosen unit, thousandths: 2.5 kg = 2500
    unit: str = Field(max_length=30)         # base unit, a standard unit or one of the item's purchase units
    amount_paise: int = Field(ge=0)          # line amount before GST
    gst_bp: int = Field(default=0, ge=0, le=2800)


class PurchaseIn(BaseModel):
    id: uuid.UUID | None = None
    outlet_id: uuid.UUID
    supplier_id: uuid.UUID | None = None
    invoice_no: str | None = Field(default=None, max_length=40)
    invoice_date: date
    lines: list[PurchaseLineIn] = Field(min_length=1, max_length=150)
    notes: str | None = Field(default=None, max_length=200)


class PrepIn(BaseModel):
    outlet_id: uuid.UUID
    stock_item_id: uuid.UUID
    made_m: int = Field(gt=0)                # how much was made, base-unit thousandths


class WasteLineIn(BaseModel):
    stock_item_id: uuid.UUID
    qty_m: int = Field(gt=0)
    reason: str = Field(min_length=2, max_length=120)


class WasteIn(BaseModel):
    outlet_id: uuid.UUID
    lines: list[WasteLineIn] = Field(min_length=1, max_length=100)


class CountLineIn(BaseModel):
    stock_item_id: uuid.UUID
    counted_m: int = Field(ge=0)


class CountIn(BaseModel):
    outlet_id: uuid.UUID
    lines: list[CountLineIn] = Field(min_length=1, max_length=500)


class LevelIn(BaseModel):
    min_level_m: int | None = Field(default=None, ge=0)


# ------------------------------------------------------------------ helpers

def _item_out(it: StockItem, show_cost: bool) -> dict:
    d = {"id": str(it.id), "name": it.name, "kind": it.kind, "unit": it.unit, "category": it.category,
         "purchase_units": it.purchase_units or [], "default_supplier_id": str(it.default_supplier_id) if it.default_supplier_id else None,
         "min_level_m": it.min_level_m, "reorder_qty_m": it.reorder_qty_m, "yield_m": it.yield_m,
         "made_on_sale": it.made_on_sale, "is_active": it.is_active}
    if show_cost:
        d["last_cost_k"] = it.last_cost_k
    return d


def _factor(item: StockItem, unit: str) -> int:
    if unit == item.unit:
        return 1
    for u in item.purchase_units or []:
        if u["name"].strip().lower() == unit.strip().lower():
            return int(u["factor"])
    std = STANDARD_UNITS.get(unit.strip().lower())
    if std and std[0] == item.unit:
        return std[1]
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{item.name} isn't counted in '{unit}'")


def _stock_item(db: Session, item_id: uuid.UUID) -> StockItem:
    return get_or_404(db, StockItem, item_id, "Stock item")


def _owner_ok(db: Session, kind: str, owner_id: uuid.UUID) -> str:
    if kind == "variant":
        v = get_or_404(db, Variant, owner_id, "Item size")
        it = db.get(Item, v.item_id)
        return f"{it.name} ({v.name})" if it else v.name
    if kind == "addon":
        return get_or_404(db, Addon, owner_id, "Add-on").name
    it = _stock_item(db, owner_id)
    if it.kind != "prep":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{it.name} is bought, not made here")
    return it.name


def _save_recipe(db: Session, auth: Auth, kind: str, owner_id: uuid.UUID, lines: list[RecipeLineIn]) -> list[dict]:
    name = _owner_ok(db, kind, owner_id)
    seen: dict[uuid.UUID, int] = {}
    for ln in lines:
        if ln.stock_item_id in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A stock item is in the recipe twice")
        si = _stock_item(db, ln.stock_item_id)
        if kind == "prep" and si.id == owner_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A prep item can't be made from itself")
        seen[ln.stock_item_id] = ln.qty_m
    if kind == "prep":
        book, _y, _o = st.prep_book(db)
        book.pop(str(owner_id), None)
        if finds_loop(str(owner_id), [str(k) for k in seen], book):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That makes a loop: one of these is itself made using this item")
    before = {str(r.stock_item_id): r.qty_m for r in db.scalars(select(RecipeLine).where(
        RecipeLine.owner_kind == kind, RecipeLine.owner_id == owner_id))}
    db.execute(delete(RecipeLine).where(RecipeLine.owner_kind == kind, RecipeLine.owner_id == owner_id))
    for sid, q in seen.items():
        db.add(RecipeLine(tenant_id=auth.tenant_id, owner_kind=kind, owner_id=owner_id, stock_item_id=sid, qty_m=q))
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "update", f"recipe_{kind}", owner_id, before,
                 {str(k): v for k, v in seen.items()} | {"_name": name})
    return [{"stock_item_id": str(k), "qty_m": v} for k, v in seen.items()]


# ------------------------------------------------------------------ stock items and suppliers

@router.get("/items")
def list_items(auth: Auth = Depends(require(Perm.stock_count)), db: Session = Depends(get_db)) -> list[dict]:
    show = auth.can(Perm.costing_view)
    return [_item_out(i, show) for i in db.scalars(select(StockItem).order_by(StockItem.category, StockItem.name))]


@router.post("/items", status_code=201)
def create_item(body: StockItemIn, auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(StockItem.id).where(func.lower(StockItem.name) == body.name.strip().lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.name} is already a stock item")
    if body.kind == "prep" and not body.yield_m:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say how much one batch makes")
    if body.kind != "prep":
        body.made_on_sale = False
    it = StockItem(tenant_id=auth.tenant_id, **{**body.model_dump(), "name": body.name.strip(),
                   "purchase_units": [u.model_dump() for u in body.purchase_units] or None})
    db.add(it)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "stock_item", it.id, after=audit.snapshot(it))
    return _item_out(it, True)


@router.patch("/items/{item_id}")
def update_item(item_id: uuid.UUID, body: StockItemIn, auth: Auth = Depends(require(Perm.inventory_manage)),
                db: Session = Depends(get_db)) -> dict:
    it = _stock_item(db, item_id)
    before = audit.snapshot(it)
    if body.unit != it.unit and db.scalar(select(StockMove.id).where(StockMove.stock_item_id == it.id).limit(1)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This item already has stock entries; its unit can't change. Make a new item.")
    if body.kind != "prep":
        body.made_on_sale = False
    for k, v in body.model_dump().items():
        setattr(it, k, v)
    it.name = body.name.strip()
    it.purchase_units = [u.model_dump() for u in body.purchase_units] or None
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "update", "stock_item", it.id, before, audit.snapshot(it))
    return _item_out(it, True)


@router.get("/suppliers")
def list_suppliers(auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> list[dict]:
    return [{"id": str(s.id), "name": s.name, "phone": s.phone, "gstin": s.gstin, "is_active": s.is_active}
            for s in db.scalars(select(Supplier).order_by(Supplier.name))]


@router.post("/suppliers", status_code=201)
def create_supplier(body: SupplierIn, auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> dict:
    s = Supplier(tenant_id=auth.tenant_id, **{**body.model_dump(), "name": body.name.strip()})
    db.add(s)
    db.flush()
    return {"id": str(s.id), "name": s.name, "phone": s.phone, "gstin": s.gstin, "is_active": s.is_active}


@router.patch("/suppliers/{supplier_id}")
def update_supplier(supplier_id: uuid.UUID, body: SupplierIn, auth: Auth = Depends(require(Perm.inventory_manage)),
                    db: Session = Depends(get_db)) -> dict:
    s = get_or_404(db, Supplier, supplier_id, "Supplier")
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    db.flush()
    return {"id": str(s.id), "name": s.name, "phone": s.phone, "gstin": s.gstin, "is_active": s.is_active}


# ------------------------------------------------------------------ recipes

@router.get("/recipes")
def all_recipes(auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> dict:
    """Every recipe, keyed "variant:<id>", "addon:<id>", "prep:<id>"."""
    out: dict[str, list] = {}
    for r in db.scalars(select(RecipeLine)):
        out.setdefault(f"{r.owner_kind}:{r.owner_id}", []).append({"stock_item_id": str(r.stock_item_id), "qty_m": r.qty_m})
    return out


@router.put("/recipes/{kind}/{owner_id}")
def put_recipe(kind: str, owner_id: uuid.UUID, body: RecipeIn, auth: Auth = Depends(require(Perm.inventory_manage)),
               db: Session = Depends(get_db)) -> list[dict]:
    if kind not in ("variant", "addon", "prep"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown recipe kind")
    return _save_recipe(db, auth, kind, owner_id, body.lines)


@router.post("/recipes/copy")
def copy_recipe(body: CopyIn, auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> dict:
    """Copy one size's recipe to others, scaled (300 ml -> 1000 ml: factor 33333)."""
    src = list(db.scalars(select(RecipeLine).where(RecipeLine.owner_kind == "variant", RecipeLine.owner_id == body.from_variant_id)))
    if not src:
        raise HTTPException(status.HTTP_409_CONFLICT, "That item size has no recipe to copy yet")
    for vid in body.to_variant_ids:
        if vid == body.from_variant_id:
            continue
        _save_recipe(db, auth, "variant", vid, [RecipeLineIn(stock_item_id=r.stock_item_id, qty_m=max(scale(r.qty_m, body.factor_bp), 1))
                                                for r in src])
    return {"copied_to": len([v for v in body.to_variant_ids if v != body.from_variant_id])}


# ------------------------------------------------------------------ purchases

def _purchase_out(db: Session, p: Purchase) -> dict:
    sup = db.get(Supplier, p.supplier_id) if p.supplier_id else None
    return {"id": str(p.id), "outlet_id": str(p.outlet_id), "supplier": sup.name if sup else None, "supplier_id": str(p.supplier_id) if p.supplier_id else None,
            "supplier_gstin": sup.gstin if sup else None, "invoice_no": p.invoice_no, "invoice_date": str(p.invoice_date),
            "amount_paise": p.amount_paise, "tax_paise": p.tax_paise, "total_paise": p.total_paise, "lines": p.lines,
            "notes": p.notes, "status": p.status, "created_at": p.created_at.isoformat() if p.created_at else None}


@router.post("/purchases", status_code=201)
def create_purchase(body: PurchaseIn, auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> dict:
    """Goods received against a supplier's bill: stock goes up, the average cost and last price update."""
    if body.id and (ex := db.get(Purchase, body.id)) is not None:
        return _purchase_out(db, ex)
    ensure_outlet_access(db, auth, body.outlet_id)
    get_or_404(db, Outlet, body.outlet_id, "Outlet")
    if body.supplier_id:
        get_or_404(db, Supplier, body.supplier_id, "Supplier")
    lines, amount, tax = [], 0, 0
    items: list[tuple[StockItem, int, int]] = []
    for ln in body.lines:
        it = _stock_item(db, ln.stock_item_id)
        factor = _factor(it, ln.unit)
        base_m = ln.qty_m * factor
        cost_k = unit_cost_k(ln.amount_paise, base_m) if ln.amount_paise else (it.last_cost_k or 0)
        t = (ln.amount_paise * ln.gst_bp + 5000) // 10000
        amount += ln.amount_paise
        tax += t
        lines.append({"stock_item_id": str(it.id), "name": it.name, "qty_m": ln.qty_m, "unit": ln.unit, "factor": factor,
                      "base_m": base_m, "base_unit": it.unit, "amount_paise": ln.amount_paise, "gst_bp": ln.gst_bp, "tax_paise": t})
        items.append((it, base_m, cost_k))
    p = Purchase(id=body.id or uuid.uuid4(), tenant_id=auth.tenant_id, outlet_id=body.outlet_id, supplier_id=body.supplier_id,
                 invoice_no=(body.invoice_no or "").strip() or None, invoice_date=body.invoice_date, business_date=business_date(),
                 amount_paise=amount, tax_paise=tax, total_paise=amount + tax, lines=lines,
                 notes=(body.notes or "").strip() or None, created_by=auth.user_id)
    db.add(p)
    db.flush()
    for it, base_m, cost_k in sorted(items, key=lambda x: str(x[0].id)):
        st.post(db, tenant_id=auth.tenant_id, outlet_id=body.outlet_id, item=it, qty_m=base_m, kind="purchase",
                ref_kind="purchase", ref_id=p.id, user_id=auth.user_id, in_cost_k=cost_k,
                note=p.invoice_no)
        if cost_k:
            it.last_cost_k = cost_k
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "purchase", p.id, after={"total_paise": p.total_paise, "lines": len(lines)})
    return _purchase_out(db, p)


@router.get("/purchases")
def list_purchases(outlet_id: uuid.UUID, d1: date | None = None, d2: date | None = None,
                   auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> list[dict]:
    ensure_outlet_access(db, auth, outlet_id)
    q = select(Purchase).where(Purchase.outlet_id == outlet_id)
    if d1:
        q = q.where(Purchase.invoice_date >= d1)
    if d2:
        q = q.where(Purchase.invoice_date <= d2)
    return [_purchase_out(db, p) for p in db.scalars(q.order_by(Purchase.invoice_date.desc(), Purchase.created_at.desc()).limit(300))]


@router.post("/purchases/{purchase_id}/cancel")
def cancel_purchase(purchase_id: uuid.UUID, auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> dict:
    """Entered by mistake: the stock it added is taken back out. The entry stays, marked cancelled."""
    p = get_or_404(db, Purchase, purchase_id, "Purchase")
    ensure_outlet_access(db, auth, p.outlet_id)
    if p.status == "cancelled":
        raise HTTPException(status.HTTP_409_CONFLICT, "Already cancelled")
    for ln in sorted(p.lines, key=lambda x: x["stock_item_id"]):
        it = db.get(StockItem, uuid.UUID(ln["stock_item_id"]))
        if it:
            st.post(db, tenant_id=p.tenant_id, outlet_id=p.outlet_id, item=it, qty_m=-ln["base_m"], kind="purchase",
                    ref_kind="purchase", ref_id=p.id, user_id=auth.user_id, note="purchase cancelled")
    p.status = "cancelled"
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "cancel", "purchase", p.id)
    return _purchase_out(db, p)


# ------------------------------------------------------------------ prep, wastage, counts

@router.post("/prep")
def make_prep(body: PrepIn, auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> dict:
    """Something made in-house: its ingredients go out, it comes in at their cost."""
    ensure_outlet_access(db, auth, body.outlet_id)
    item = _stock_item(db, body.stock_item_id)
    if item.kind != "prep" or not item.yield_m:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{item.name} isn't set up as a prep item with a batch size")
    if item.made_on_sale:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{item.name} is made fresh when sold: its ingredients come out with each sale, so there's no batch to enter")
    rec = st.recipes(db, "prep", [item.id]).get(item.id, [])
    if not rec:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Add the recipe for one batch of {item.name} first")
    ref = uuid.uuid4()
    cost = 0
    for u in sorted(rec, key=lambda x: x.stock_item_id):
        ing = db.get(StockItem, uuid.UUID(u.stock_item_id))
        used = -((u.qty_milli * body.made_m + item.yield_m // 2) // item.yield_m)
        mv = st.post(db, tenant_id=auth.tenant_id, outlet_id=body.outlet_id, item=ing, qty_m=used, kind="prep_out",
                     ref_kind="prep", ref_id=ref, user_id=auth.user_id, note=f"for {item.name}")
        cost += -mv.value_paise
    in_cost_k = unit_cost_k(cost, body.made_m) if cost else 0
    st.post(db, tenant_id=auth.tenant_id, outlet_id=body.outlet_id, item=item, qty_m=body.made_m, kind="prep_in",
            ref_kind="prep", ref_id=ref, user_id=auth.user_id, in_cost_k=in_cost_k)
    db.flush()
    out = {"made_m": body.made_m, "unit": item.unit}
    if auth.can(Perm.costing_view):
        out["cost_paise"] = cost
    return out


@router.post("/wastage")
def record_wastage(body: WasteIn, auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> dict:
    ensure_outlet_access(db, auth, body.outlet_id)
    ref = uuid.uuid4()
    total = 0
    for ln in sorted(body.lines, key=lambda x: str(x.stock_item_id)):
        it = _stock_item(db, ln.stock_item_id)
        mv = st.post(db, tenant_id=auth.tenant_id, outlet_id=body.outlet_id, item=it, qty_m=-ln.qty_m, kind="waste",
                     ref_kind="waste", ref_id=ref, user_id=auth.user_id, note=ln.reason.strip())
        total += -mv.value_paise
    db.flush()
    return {"lines": len(body.lines), **({"value_paise": total} if auth.can(Perm.costing_view) else {})}


@router.post("/counts")
def stock_count(body: CountIn, auth: Auth = Depends(require(Perm.stock_count)), db: Session = Depends(get_db)) -> dict:
    """Closing count. Stock is set to what was counted; the difference is kept for the manager.
    Staff don't see the expected stock or the difference."""
    ensure_outlet_access(db, auth, body.outlet_id)
    cid = uuid.uuid4()
    lines = []
    for ln in sorted(body.lines, key=lambda x: str(x.stock_item_id)):
        it = _stock_item(db, ln.stock_item_id)
        lv = st.level(db, auth.tenant_id, body.outlet_id, it.id)
        expected = lv.qty_m
        diff = ln.counted_m - expected
        val = 0
        if diff:
            mv = st.post(db, tenant_id=auth.tenant_id, outlet_id=body.outlet_id, item=it, qty_m=diff, kind="count",
                         ref_kind="count", ref_id=cid, user_id=auth.user_id)
            val = mv.value_paise
        lv.counted_at = datetime.now(timezone.utc)
        lines.append({"stock_item_id": str(it.id), "name": it.name, "unit": it.unit, "expected_m": expected,
                      "counted_m": ln.counted_m, "diff_m": diff, "value_paise": val})
    db.add(StockCount(id=cid, tenant_id=auth.tenant_id, outlet_id=body.outlet_id, business_date=business_date(),
                      lines=lines, counted_by=auth.user_id))
    db.flush()
    if not auth.can(Perm.costing_view):
        return {"saved": len(lines)}
    short = [x for x in lines if x["diff_m"] < 0]
    return {"saved": len(lines), "lines": lines, "short_value_paise": -sum(x["value_paise"] for x in short)}


# ------------------------------------------------------------------ stock on hand, reorder, costing

@router.get("/stock")
def stock_on_hand(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.stock_count)), db: Session = Depends(get_db)) -> dict:
    ensure_outlet_access(db, auth, outlet_id)
    show = auth.can(Perm.costing_view)
    manager = auth.can(Perm.inventory_manage)
    levels = {lv.stock_item_id: lv for lv in db.scalars(select(StockLevel).where(StockLevel.outlet_id == outlet_id))}
    rows, value = [], 0
    # Made fresh when sold: never in stock, so not listed or counted.
    for it in db.scalars(select(StockItem).where(StockItem.is_active.is_(True), StockItem.made_on_sale.is_(False))
                         .order_by(StockItem.category, StockItem.name)):
        lv = levels.get(it.id)
        qty = lv.qty_m if lv else 0
        mn = lv.min_level_m if lv and lv.min_level_m is not None else it.min_level_m
        row = {"id": str(it.id), "name": it.name, "kind": it.kind, "unit": it.unit, "category": it.category,
               "counted_at": lv.counted_at.isoformat() if lv and lv.counted_at else None}
        if manager:
            row |= {"qty_m": qty, "min_level_m": mn, "low": mn > 0 and qty <= mn, "negative": qty < 0}
        if show:
            cost = (lv.cost_k if lv and lv.cost_k else it.last_cost_k)
            row |= {"cost_k": cost, "value_paise": value_paise(max(qty, 0), cost)}
            value += row["value_paise"]
        rows.append(row)
    out: dict = {"items": rows}
    if show:
        out["value_paise"] = value
    return out


@router.put("/stock/{outlet_id}/{item_id}")
def set_min_level(outlet_id: uuid.UUID, item_id: uuid.UUID, body: LevelIn, auth: Auth = Depends(require(Perm.inventory_manage)),
                  db: Session = Depends(get_db)) -> dict:
    ensure_outlet_access(db, auth, outlet_id)
    lv = st.level(db, auth.tenant_id, outlet_id, _stock_item(db, item_id).id)
    lv.min_level_m = body.min_level_m
    db.flush()
    return {"ok": True}


@router.get("/reorder")
def reorder_list(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> list[dict]:
    """What's at or below its minimum, with a suggested quantity, grouped by usual supplier. Edit before ordering."""
    ensure_outlet_access(db, auth, outlet_id)
    levels = {lv.stock_item_id: lv for lv in db.scalars(select(StockLevel).where(StockLevel.outlet_id == outlet_id))}
    sups = {s.id: s for s in db.scalars(select(Supplier))}
    groups: dict[str, dict] = {}
    for it in db.scalars(select(StockItem).where(StockItem.is_active.is_(True), StockItem.made_on_sale.is_(False)).order_by(StockItem.name)):
        lv = levels.get(it.id)
        qty = lv.qty_m if lv else 0
        mn = lv.min_level_m if lv and lv.min_level_m is not None else it.min_level_m
        need = reorder_qty(qty, mn, it.reorder_qty_m)
        if not need:
            continue
        sup = sups.get(it.default_supplier_id) if it.default_supplier_id else None
        g = groups.setdefault(str(sup.id) if sup else "", {"supplier_id": str(sup.id) if sup else None,
                                                           "supplier": sup.name if sup else "No usual supplier",
                                                           "phone": sup.phone if sup else None, "lines": []})
        units = it.purchase_units or []
        big = max(units, key=lambda u: u["factor"]) if units else None
        g["lines"].append({"stock_item_id": str(it.id), "name": it.name, "unit": it.unit, "on_hand_m": qty, "min_level_m": mn,
                           "order_m": need, "kind": it.kind,
                           "in_purchase_unit": ({"unit": big["name"], "qty": -(-need // (big["factor"] * M))} if big else None)})
    return sorted(groups.values(), key=lambda g: (g["supplier_id"] is None, g["supplier"]))


@router.get("/costing")
def costing(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.costing_view)), db: Session = Depends(get_db)) -> list[dict]:
    """Cost of making each item size from its recipe at today's average costs, against its price before GST."""
    ensure_outlet_access(db, auth, outlet_id)
    outlet = get_or_404(db, Outlet, outlet_id, "Outlet")
    costs: dict[str, int] = {str(i.id): i.last_cost_k for i in db.scalars(select(StockItem))}
    for lv in db.scalars(select(StockLevel).where(StockLevel.outlet_id == outlet_id)):
        if lv.cost_k:
            costs[str(lv.stock_item_id)] = lv.cost_k
    # Prep items not made yet (or made fresh per order): cost them from their batch recipe.
    book, yields, on_sale = st.prep_book(db)
    for sid in book:
        if sid in on_sale or not costs.get(sid):
            costs[sid] = rolled_cost_k(sid, book, yields, {k: v for k, v in costs.items() if k not in on_sale})
    taxes = {t.id: t for t in db.scalars(select(TaxGroup))}
    overrides = {o.variant_id: o for o in db.scalars(select(OutletVariantOverride).where(OutletVariantOverride.outlet_id == outlet_id))}
    rows = []
    items = {i.id: i for i in db.scalars(select(Item).where(Item.brand_id == outlet.brand_id))}
    variants = list(db.scalars(select(Variant).where(Variant.item_id.in_(list(items)))))
    rec = st.recipes(db, "variant", [v.id for v in variants])
    for v in variants:
        it = items[v.item_id]
        ov = overrides.get(v.id)
        price = ov.price_paise if ov and ov.price_paise is not None else v.price_paise
        tg = taxes.get(it.tax_group_id) if it.tax_group_id else None
        rate = (tg.cgst_bp + tg.sgst_bp + tg.igst_bp) if tg else 0
        net = (price * 10000 + (10000 + rate) // 2) // (10000 + rate) if tg and tg.is_inclusive else price
        r = rec.get(v.id, [])
        cost = recipe_cost(r, costs)
        rows.append({"variant_id": str(v.id), "item": it.name, "variant": v.name, "price_paise": price, "net_price_paise": net,
                     "cost_paise": cost if r else None, "margin_bp": margin_bp(net, cost) if r else None,
                     "food_cost_bp": (cost * 10000 + net // 2) // net if r and net else None, "has_recipe": bool(r),
                     "missing_cost": [u.stock_item_id for u in r if not costs.get(u.stock_item_id)]})
    return sorted(rows, key=lambda x: (x["has_recipe"], x["margin_bp"] if x["margin_bp"] is not None else 10**9, x["item"]))


@router.get("/prep-costs")
def prep_costs(outlet_id: uuid.UUID, auth: Auth = Depends(require(Perm.costing_view)), db: Session = Depends(get_db)) -> list[dict]:
    """What each sauce, chutney or base costs: per batch and per kg / litre / piece.
    'made' = the real average of the batches made here; 'recipe' = from its ingredients at today's costs."""
    ensure_outlet_access(db, auth, outlet_id)
    items = {str(i.id): i for i in db.scalars(select(StockItem))}
    actual: dict[str, int] = {k: i.last_cost_k for k, i in items.items() if i.kind == "raw" and i.last_cost_k}
    for lv in db.scalars(select(StockLevel).where(StockLevel.outlet_id == outlet_id)):
        if lv.cost_k:
            actual[str(lv.stock_item_id)] = lv.cost_k
    book, yields, on_sale = st.prep_book(db)
    rows = []
    for sid, it in items.items():
        if it.kind != "prep" or not it.is_active:
            continue
        from_recipe = rolled_cost_k(sid, book, yields, {k: v for k, v in actual.items() if k != sid and k not in on_sale})
        made = actual.get(sid) if sid not in on_sale else None
        per_k = made or from_recipe
        missing = [items[u.stock_item_id].name for u in book.get(sid, []) if u.stock_item_id in items
                   and not (actual.get(u.stock_item_id) or (items[u.stock_item_id].kind == "prep" and book.get(u.stock_item_id)))]
        big = {"g": ("kg", 1000), "ml": ("litre", 1000), "pc": ("piece", 1)}[it.unit]
        rows.append({"id": sid, "name": it.name, "unit": it.unit, "yield_m": it.yield_m, "made_on_sale": it.made_on_sale,
                     "has_recipe": bool(book.get(sid)), "source": "made" if made else "recipe",
                     "batch_cost_paise": value_paise(it.yield_m or 0, per_k) if it.yield_m else None,
                     "per_unit": big[0], "per_unit_paise": value_paise(big[1] * M, per_k), "recipe_per_unit_paise": value_paise(big[1] * M, from_recipe),
                     "missing_cost": missing})
    return sorted(rows, key=lambda r: r["name"])


@router.get("/moves")
def moves(outlet_id: uuid.UUID, stock_item_id: uuid.UUID | None = None, d1: date | None = None, d2: date | None = None,
          auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> list[dict]:
    ensure_outlet_access(db, auth, outlet_id)
    q = select(StockMove).where(StockMove.outlet_id == outlet_id)
    if stock_item_id:
        q = q.where(StockMove.stock_item_id == stock_item_id)
    if d1:
        q = q.where(StockMove.business_date >= d1)
    if d2:
        q = q.where(StockMove.business_date <= d2)
    show = auth.can(Perm.costing_view)
    names = {i.id: (i.name, i.unit) for i in db.scalars(select(StockItem))}
    return [{"at": m.created_at.isoformat() if m.created_at else None, "date": str(m.business_date), "item": names.get(m.stock_item_id, ("?", ""))[0],
             "unit": names.get(m.stock_item_id, ("?", ""))[1], "kind": m.kind, "qty_m": m.qty_m, "note": m.note, "ref_kind": m.ref_kind,
             **({"value_paise": m.value_paise} if show else {})}
            for m in db.scalars(q.order_by(StockMove.created_at.desc()).limit(500))]


@router.get("/addons")
def addon_list(auth: Auth = Depends(require(Perm.inventory_manage)), db: Session = Depends(get_db)) -> list[dict]:
    """Add-ons, for giving them a recipe (e.g. extra choco chips: 10 g)."""
    return [{"id": str(a.id), "name": a.name, "group": g}
            for a, g in db.execute(select(Addon, AddonGroup.name).join(AddonGroup, AddonGroup.id == Addon.group_id).order_by(AddonGroup.name, Addon.name))]


UNITS_HELP = {"base_units": list(BASE_UNITS), "standard": {k: {"base": v[0], "factor": v[1]} for k, v in STANDARD_UNITS.items()}}


@router.get("/units")
def units(auth: Auth = Depends(require(Perm.stock_count))) -> dict:
    return UNITS_HELP

