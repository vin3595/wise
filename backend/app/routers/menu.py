"""Master menu per brand, tax groups, add-ons, and outlet-level overrides.

Nothing on the menu is hard-deleted: switch it off with is_active so old bills and reports
keep pointing at real rows.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..core.menu_resolve import Override, resolve_variant
from ..core.permissions import Perm
from ..core.tax import TaxGroup as TaxCalc
from ..deps import Auth, ensure_outlet_access, get_auth, get_db, require
from ..models import (
    Addon, AddonGroup, Brand, Category, Item, ItemAddonGroup, Outlet, OutletVariantOverride, PrepArea, TaxGroup,
    Variant,
)
from ..schemas import (
    AddonGroupIn, AddonGroupOut, AddonOut, BrandMenuOut, CategoryIn, CategoryOut, ItemIn, ItemOut,
    OutletMenuItem, OutletMenuOut, OutletMenuVariant, OverrideIn, PrepAreaAssign, PrepAreaOut, TaxGroupIn, TaxGroupOut, VariantOut,
)
from ..services import audit
from ..services.uploads import save_image
from ..services.menu_view import build_outlet_menu, group_out as _group_out
from .common import get_or_404

router = APIRouter(prefix="/menu", tags=["menu"])


# ------------------------------------------------------------------ tax groups

def _validate_tax(body: TaxGroupIn) -> None:
    try:
        TaxCalc(body.cgst_bp, body.sgst_bp, body.igst_bp, body.is_inclusive)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


def _single_default(db: Session, keep: TaxGroup) -> None:
    if keep.is_default:
        db.execute(update(TaxGroup).where(TaxGroup.id != keep.id).values(is_default=False))


@router.get("/tax-groups", response_model=list[TaxGroupOut])
def list_tax_groups(auth: Auth = Depends(require(Perm.menu_view)), db: Session = Depends(get_db)):
    return list(db.scalars(select(TaxGroup).order_by(TaxGroup.name)))


@router.post("/tax-groups", response_model=TaxGroupOut, status_code=201)
def create_tax_group(body: TaxGroupIn, auth: Auth = Depends(require(Perm.menu_manage)),
                     db: Session = Depends(get_db)):
    _validate_tax(body)
    tg = TaxGroup(tenant_id=auth.tenant_id, **body.model_dump())
    db.add(tg)
    db.flush()
    _single_default(db, tg)
    audit.record(db, auth.tenant_id, auth.user_id, "create", "tax_group", tg.id, after=audit.snapshot(tg))
    return tg


@router.patch("/tax-groups/{tg_id}", response_model=TaxGroupOut)
def update_tax_group(tg_id: uuid.UUID, body: TaxGroupIn, auth: Auth = Depends(require(Perm.menu_manage)),
                     db: Session = Depends(get_db)):
    _validate_tax(body)
    tg = get_or_404(db, TaxGroup, tg_id, "Tax group")
    before = audit.snapshot(tg)
    for k, v in body.model_dump().items():
        setattr(tg, k, v)
    db.flush()
    _single_default(db, tg)
    audit.record(db, auth.tenant_id, auth.user_id, "update", "tax_group", tg.id, before, audit.snapshot(tg))
    return tg


# ------------------------------------------------------------------ categories

@router.get("/categories", response_model=list[CategoryOut])
def list_categories(brand_id: uuid.UUID, auth: Auth = Depends(require(Perm.menu_view)),
                    db: Session = Depends(get_db)):
    return list(db.scalars(select(Category).where(Category.brand_id == brand_id)
                           .order_by(Category.sort_order, Category.name)))


def _check_category(db: Session, body: CategoryIn, self_id: uuid.UUID | None = None) -> None:
    get_or_404(db, Brand, body.brand_id, "Brand")
    if body.parent_id:
        parent = get_or_404(db, Category, body.parent_id, "Parent category")
        if parent.brand_id != body.brand_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Parent category belongs to another brand")
        if parent.id == self_id or parent.parent_id is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only one level of sub-categories is allowed")


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryIn, auth: Auth = Depends(require(Perm.menu_manage)),
                    db: Session = Depends(get_db)):
    _check_category(db, body)
    cat = Category(tenant_id=auth.tenant_id, **body.model_dump())
    db.add(cat)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "create", "category", cat.id, after=audit.snapshot(cat))
    return cat


@router.patch("/categories/{cat_id}", response_model=CategoryOut)
def update_category(cat_id: uuid.UUID, body: CategoryIn, auth: Auth = Depends(require(Perm.menu_manage)),
                    db: Session = Depends(get_db)):
    cat = get_or_404(db, Category, cat_id, "Category")
    if body.brand_id != cat.brand_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A category can't move to another brand")
    _check_category(db, body, cat.id)
    before = audit.snapshot(cat)
    for k, v in body.model_dump().items():
        setattr(cat, k, v)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "update", "category", cat.id, before, audit.snapshot(cat))
    return cat


# ------------------------------------------------------------------ items

def _item_out(db: Session, item: Item) -> ItemOut:
    out = ItemOut.model_validate(item)
    out.variants = [VariantOut.model_validate(v) for v in db.scalars(
        select(Variant).where(Variant.item_id == item.id).order_by(Variant.sort_order, Variant.name))]
    out.addon_group_ids = list(db.scalars(select(ItemAddonGroup.group_id).where(ItemAddonGroup.item_id == item.id)))
    return out


def _check_item(db: Session, body: ItemIn) -> None:
    get_or_404(db, Brand, body.brand_id, "Brand")
    cat = get_or_404(db, Category, body.category_id, "Category")
    if cat.brand_id != body.brand_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Category belongs to another brand")
    if body.tax_group_id:
        get_or_404(db, TaxGroup, body.tax_group_id, "Tax group")
    if body.prep_area_id:
        get_or_404(db, PrepArea, body.prep_area_id, "Preparation area")
    for gid in body.addon_group_ids:
        group = get_or_404(db, AddonGroup, gid, "Add-on group")
        if group.brand_id != body.brand_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Add-on group belongs to another brand")
    names = [v.name.strip().lower() for v in body.variants]
    if len(names) != len(set(names)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Variant names must be unique within an item")


def _sync_variants(db: Session, item: Item, variants) -> None:
    existing = {v.id: v for v in db.scalars(select(Variant).where(Variant.item_id == item.id))}
    seen: set[uuid.UUID] = set()
    for i, v in enumerate(variants):
        data = v.model_dump(exclude={"id"})
        data.setdefault("sort_order", i)
        if v.id is not None:
            row = existing.get(v.id)
            if row is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Variant doesn't belong to this item")
            for k, val in data.items():
                setattr(row, k, val)
            seen.add(row.id)
        else:
            db.add(Variant(tenant_id=item.tenant_id, item_id=item.id, **data))
    # Variants left out of the update are switched off, never deleted.
    for vid, row in existing.items():
        if vid not in seen:
            row.is_active = False


def _sync_addon_links(db: Session, item: Item, group_ids: list[uuid.UUID]) -> None:
    db.execute(delete(ItemAddonGroup).where(ItemAddonGroup.item_id == item.id))
    for gid in dict.fromkeys(group_ids):
        db.add(ItemAddonGroup(item_id=item.id, group_id=gid, tenant_id=item.tenant_id))


@router.get("/items", response_model=list[ItemOut])
def list_items(brand_id: uuid.UUID, auth: Auth = Depends(require(Perm.menu_view)), db: Session = Depends(get_db)):
    items = db.scalars(select(Item).where(Item.brand_id == brand_id).order_by(Item.sort_order, Item.name))
    return [_item_out(db, i) for i in items]


@router.get("/items/{item_id}", response_model=ItemOut)
def get_item(item_id: uuid.UUID, auth: Auth = Depends(require(Perm.menu_view)), db: Session = Depends(get_db)):
    return _item_out(db, get_or_404(db, Item, item_id, "Item"))


@router.post("/items", response_model=ItemOut, status_code=201)
def create_item(body: ItemIn, auth: Auth = Depends(require(Perm.menu_manage)), db: Session = Depends(get_db)):
    _check_item(db, body)
    if any(v.id for v in body.variants):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "New items can't reference existing variants")
    item = Item(tenant_id=auth.tenant_id, **body.model_dump(exclude={"variants", "addon_group_ids"}))
    db.add(item)
    db.flush()
    _sync_variants(db, item, body.variants)
    _sync_addon_links(db, item, body.addon_group_ids)
    db.flush()
    out = _item_out(db, item)
    audit.record(db, auth.tenant_id, auth.user_id, "create", "item", item.id, after=out.model_dump(mode="json"))
    return out


@router.put("/items/{item_id}", response_model=ItemOut)
def update_item(item_id: uuid.UUID, body: ItemIn, auth: Auth = Depends(require(Perm.menu_manage)),
                db: Session = Depends(get_db)):
    item = get_or_404(db, Item, item_id, "Item")
    if body.brand_id != item.brand_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "An item can't move to another brand")
    _check_item(db, body)
    before = _item_out(db, item).model_dump(mode="json")
    for k, v in body.model_dump(exclude={"variants", "addon_group_ids"}).items():
        setattr(item, k, v)
    _sync_variants(db, item, body.variants)
    _sync_addon_links(db, item, body.addon_group_ids)
    db.flush()
    out = _item_out(db, item)
    audit.record(db, auth.tenant_id, auth.user_id, "update", "item", item.id, before, out.model_dump(mode="json"))
    return out


@router.post("/items/prep-area")
def set_prep_area(body: PrepAreaAssign, auth: Auth = Depends(require(Perm.menu_manage)),
                  db: Session = Depends(get_db)) -> dict:
    """Which preparation area (and so which KOT printer and kitchen screen) items go to, for many at once."""
    if body.prep_area_id is not None:
        get_or_404(db, PrepArea, body.prep_area_id, "Preparation area")
    items = list(db.scalars(select(Item).where(Item.id.in_(body.item_ids))))
    if len(items) != len(set(body.item_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    for it in items:
        it.prep_area_id = body.prep_area_id
    audit.record(db, auth.tenant_id, auth.user_id, "set_prep_area", "item", None,
                 after={"items": [str(i.id) for i in items], "prep_area_id": str(body.prep_area_id) if body.prep_area_id else None})
    return {"updated": len(items)}


@router.post("/items/{item_id}/image", response_model=ItemOut)
def upload_item_image(item_id: uuid.UUID, file: UploadFile = File(...), auth: Auth = Depends(require(Perm.menu_manage)),
                      db: Session = Depends(get_db)):
    """Food photo shown on the QR menu and the kiosk."""
    item = get_or_404(db, Item, item_id, "Item")
    item.image_url = save_image(file, auth.tenant_id, f"item-{item.id.hex[:8]}")
    audit.record(db, auth.tenant_id, auth.user_id, "update", "item_image", item.id, after={"image_url": item.image_url})
    return _item_out(db, item)


# ------------------------------------------------------------------ add-ons

def _sync_addons(db: Session, group: AddonGroup, addons) -> None:
    existing = {a.id: a for a in db.scalars(select(Addon).where(Addon.group_id == group.id))}
    seen = set()
    for a in addons:
        data = a.model_dump(exclude={"id"})
        if a.id is not None:
            row = existing.get(a.id)
            if row is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Add-on doesn't belong to this group")
            for k, v in data.items():
                setattr(row, k, v)
            seen.add(row.id)
        else:
            db.add(Addon(tenant_id=group.tenant_id, group_id=group.id, **data))
    for aid, row in existing.items():
        if aid not in seen:
            row.is_active = False


def _check_group(body: AddonGroupIn) -> None:
    if body.min_select > body.max_select:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Minimum picks can't be more than maximum")


@router.get("/addon-groups", response_model=list[AddonGroupOut])
def list_addon_groups(brand_id: uuid.UUID, auth: Auth = Depends(require(Perm.menu_view)),
                      db: Session = Depends(get_db)):
    return [_group_out(db, g) for g in db.scalars(
        select(AddonGroup).where(AddonGroup.brand_id == brand_id).order_by(AddonGroup.name))]


@router.post("/addon-groups", response_model=AddonGroupOut, status_code=201)
def create_addon_group(body: AddonGroupIn, auth: Auth = Depends(require(Perm.menu_manage)),
                       db: Session = Depends(get_db)):
    _check_group(body)
    get_or_404(db, Brand, body.brand_id, "Brand")
    g = AddonGroup(tenant_id=auth.tenant_id, **body.model_dump(exclude={"addons"}))
    db.add(g)
    db.flush()
    _sync_addons(db, g, body.addons)
    db.flush()
    out = _group_out(db, g)
    audit.record(db, auth.tenant_id, auth.user_id, "create", "addon_group", g.id, after=out.model_dump(mode="json"))
    return out


@router.put("/addon-groups/{group_id}", response_model=AddonGroupOut)
def update_addon_group(group_id: uuid.UUID, body: AddonGroupIn, auth: Auth = Depends(require(Perm.menu_manage)),
                       db: Session = Depends(get_db)):
    _check_group(body)
    g = get_or_404(db, AddonGroup, group_id, "Add-on group")
    if body.brand_id != g.brand_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A group can't move to another brand")
    before = _group_out(db, g).model_dump(mode="json")
    for k, v in body.model_dump(exclude={"addons"}).items():
        setattr(g, k, v)
    _sync_addons(db, g, body.addons)
    db.flush()
    out = _group_out(db, g)
    audit.record(db, auth.tenant_id, auth.user_id, "update", "addon_group", g.id, before, out.model_dump(mode="json"))
    return out


# ------------------------------------------------------------------ whole menus

@router.get("/brands/{brand_id}", response_model=BrandMenuOut)
def brand_menu(brand_id: uuid.UUID, auth: Auth = Depends(require(Perm.menu_view)), db: Session = Depends(get_db)):
    get_or_404(db, Brand, brand_id, "Brand")
    return BrandMenuOut(
        brand_id=brand_id,
        categories=[CategoryOut.model_validate(c) for c in list_categories(brand_id, auth, db)],
        items=list_items(brand_id, auth, db),
        addon_groups=list_addon_groups(brand_id, auth, db),
        tax_groups=[TaxGroupOut.model_validate(t) for t in list_tax_groups(auth, db)],
        prep_areas=[PrepAreaOut.model_validate(p) for p in db.scalars(
            select(PrepArea).order_by(PrepArea.sort_order, PrepArea.name))],
    )


@router.get("/outlets/{outlet_id}", response_model=OutletMenuOut)
def outlet_menu(outlet_id: uuid.UUID, include_unavailable: bool = False,
                auth: Auth = Depends(require(Perm.menu_view)), db: Session = Depends(get_db)):
    """The menu as this outlet sells it: master menu + this outlet's price/availability overrides."""
    outlet = get_or_404(db, Outlet, outlet_id, "Outlet")
    ensure_outlet_access(db, auth, outlet_id)
    return build_outlet_menu(db, outlet, include_unavailable)


@router.put("/outlets/{outlet_id}/variants/{variant_id}")
def set_override(outlet_id: uuid.UUID, variant_id: uuid.UUID, body: OverrideIn,
                 auth: Auth = Depends(require(Perm.menu_override)), db: Session = Depends(get_db)) -> dict:
    outlet = get_or_404(db, Outlet, outlet_id, "Outlet")
    ensure_outlet_access(db, auth, outlet_id)
    variant = get_or_404(db, Variant, variant_id, "Variant")
    item = db.get(Item, variant.item_id)
    if item.brand_id != outlet.brand_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This item isn't on this outlet's brand menu")
    row = db.scalar(select(OutletVariantOverride).where(
        OutletVariantOverride.outlet_id == outlet_id, OutletVariantOverride.variant_id == variant_id))
    before = audit.snapshot(row) if row else None
    if body.is_active is None and body.price_paise is None:
        if row is not None and row.sold_out_on is None:
            db.delete(row)
        elif row is not None:            # keep today's "sold out"; just clear the price and on/off
            row.is_active = row.price_paise = None
        audit.record(db, auth.tenant_id, auth.user_id, "clear", "override", variant_id, before, None)
        return {"cleared": True}
    if row is None:
        row = OutletVariantOverride(tenant_id=auth.tenant_id, outlet_id=outlet_id, variant_id=variant_id)
        db.add(row)
    row.is_active = body.is_active
    row.price_paise = body.price_paise
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "set", "override", variant_id, before, audit.snapshot(row))
    return {"cleared": False, "is_active": row.is_active, "price_paise": row.price_paise}


# ------------------------------------------------------------------ sold out today (counter or kitchen)

class SoldOutIn(BaseModel):
    variant_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    sold_out: bool


def _can_mark_sold_out(auth: Auth) -> None:
    if not (auth.can(Perm.billing) or auth.can(Perm.kds_update) or auth.can(Perm.menu_override)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't mark items sold out")


@router.post("/menu/outlets/{outlet_id}/sold-out")
def mark_sold_out(outlet_id: uuid.UUID, body: SoldOutIn, auth: Auth = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    """Sold out for the rest of today: greyed on the counter, gone from the QR menu and kiosk, refused on
    new orders. It comes back by itself the next business day."""
    from ..core.kds import business_date
    _can_mark_sold_out(auth)
    outlet = get_or_404(db, Outlet, outlet_id, "Outlet")
    ensure_outlet_access(db, auth, outlet_id)
    today = business_date()
    names = []
    for vid in dict.fromkeys(body.variant_ids):
        variant = get_or_404(db, Variant, vid, "Item size")
        item = db.get(Item, variant.item_id)
        if item is None or item.brand_id != outlet.brand_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This item isn't on this outlet's menu")
        row = db.scalar(select(OutletVariantOverride).where(
            OutletVariantOverride.outlet_id == outlet_id, OutletVariantOverride.variant_id == vid))
        if row is None:
            if not body.sold_out:
                continue
            row = OutletVariantOverride(tenant_id=auth.tenant_id, outlet_id=outlet_id, variant_id=vid)
            db.add(row)
        row.sold_out_on = today if body.sold_out else None
        names.append(f"{item.name} ({variant.name})" if variant.name != "Regular" else item.name)
    db.flush()
    audit.record(db, auth.tenant_id, auth.user_id, "sold_out" if body.sold_out else "back_on", "menu", outlet_id,
                 after={"items": names})
    return {"sold_out": body.sold_out, "items": names}


@router.get("/menu/outlets/{outlet_id}/sold-out")
def list_sold_out(outlet_id: uuid.UUID, auth: Auth = Depends(get_auth), db: Session = Depends(get_db)) -> list[dict]:
    from ..core.kds import business_date
    _can_mark_sold_out(auth)
    ensure_outlet_access(db, auth, outlet_id)
    out = []
    for row in db.scalars(select(OutletVariantOverride).where(OutletVariantOverride.outlet_id == outlet_id,
                                                              OutletVariantOverride.sold_out_on == business_date())):
        v = db.get(Variant, row.variant_id)
        it = db.get(Item, v.item_id) if v else None
        if v and it:
            out.append({"variant_id": str(v.id), "item": it.name, "variant": v.name})
    return sorted(out, key=lambda x: (x["item"], x["variant"]))
