"""Outlet menu as sold (master menu + outlet overrides). Shared by staff screens and the QR menu."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.menu_resolve import Override, resolve_variant
from ..models import Addon, AddonGroup, Category, Item, ItemAddonGroup, Outlet, OutletVariantOverride, Variant
from ..schemas import AddonGroupOut, AddonOut, CategoryOut, OutletMenuItem, OutletMenuOut, OutletMenuVariant


def group_out(db: Session, g: AddonGroup) -> AddonGroupOut:
    out = AddonGroupOut.model_validate(g)
    out.addons = [AddonOut.model_validate(a) for a in db.scalars(
        select(Addon).where(Addon.group_id == g.id).order_by(Addon.name))]
    return out


def build_outlet_menu(db: Session, outlet: Outlet, include_unavailable: bool = False, hide_sold_out: bool = False) -> OutletMenuOut:
    """hide_sold_out: for customers (QR menu, kiosk), sold-out sizes simply aren't there today."""
    from ..core.kds import business_date
    today = business_date()
    outlet_id = outlet.id
    cats = list(db.scalars(select(Category).where(Category.brand_id == outlet.brand_id)
                           .order_by(Category.sort_order, Category.name)))
    cat_active = {c.id: c.is_active for c in cats}
    rows = list(db.scalars(select(OutletVariantOverride).where(OutletVariantOverride.outlet_id == outlet_id)))
    overrides = {o.variant_id: Override(o.is_active, o.price_paise) for o in rows if o.is_active is not None or o.price_paise is not None}
    sold_out = {o.variant_id for o in rows if o.sold_out_on == today}
    groups = {g.id: group_out(db, g) for g in db.scalars(
        select(AddonGroup).where(AddonGroup.brand_id == outlet.brand_id, AddonGroup.is_active))}
    links: dict[uuid.UUID, list[uuid.UUID]] = {}
    for link in db.scalars(select(ItemAddonGroup)):
        links.setdefault(link.item_id, []).append(link.group_id)
    items_out: list[OutletMenuItem] = []
    for item in db.scalars(select(Item).where(Item.brand_id == outlet.brand_id).order_by(Item.sort_order, Item.name)):
        variants = []
        for v in db.scalars(select(Variant).where(Variant.item_id == item.id).order_by(Variant.sort_order)):
            ov = overrides.get(v.id)
            available, price = resolve_variant(v.is_active, v.price_paise, item.is_active,
                                               cat_active.get(item.category_id, False), ov)
            if hide_sold_out and v.id in sold_out:
                continue
            if available or include_unavailable:
                variants.append(OutletMenuVariant(
                    variant_id=v.id, name=v.name, master_price_paise=v.price_paise, price_paise=price,
                    available=available, overridden=ov is not None, sold_out=v.id in sold_out))
        if variants:
            items_out.append(OutletMenuItem(
                item_id=item.id, category_id=item.category_id, name=item.name, short_code=item.short_code,
                food_type=item.food_type, tax_group_id=item.tax_group_id, prep_area_id=item.prep_area_id,
                image_url=item.image_url, description=item.description, variants=variants,
                addon_groups=[groups[g] for g in links.get(item.id, []) if g in groups]))
    return OutletMenuOut(outlet_id=outlet_id, brand_id=outlet.brand_id,
                         categories=[CategoryOut.model_validate(c) for c in cats if c.is_active or include_unavailable],
                         items=items_out)


