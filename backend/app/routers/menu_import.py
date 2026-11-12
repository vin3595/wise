"""Excel template download and menu import (dry run first, then commit)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.menu_import import build_template, parse_menu_workbook
from ..core.permissions import Perm
from ..deps import Auth, get_db, require
from ..models import Addon, AddonGroup, Brand, Category, Item, ItemAddonGroup, PrepArea, TaxGroup, Variant
from ..schemas import ImportIssue, ImportResult
from ..services import audit
from .common import get_or_404

router = APIRouter(prefix="/menu/import", tags=["menu-import"])

MAX_FILE_BYTES = 5_000_000
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/template")
def template(auth: Auth = Depends(require(Perm.menu_manage))) -> Response:
    return Response(build_template(), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="wise-menu-template.xlsx"'})


@router.post("", response_model=ImportResult)
def import_menu(brand_id: uuid.UUID, dry_run: bool = Query(default=True), file: UploadFile = File(...),
                auth: Auth = Depends(require(Perm.menu_manage)), db: Session = Depends(get_db)) -> ImportResult:
    """Upserts by name: existing categories/items/variants with the same name are updated,
    new ones are created, nothing is deleted. Run with dry_run=true first to see errors."""
    get_or_404(db, Brand, brand_id, "Brand")
    data = file.file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File must be under 5 MB")

    tax_groups = {t.name: t for t in db.scalars(select(TaxGroup).where(TaxGroup.is_active))}
    parsed = parse_menu_workbook(data, known_tax_groups=set(tax_groups))
    result = ImportResult(
        dry_run=dry_run, ok=parsed.ok, summary=parsed.summary(),
        errors=[ImportIssue(**e.__dict__) for e in parsed.errors],
        warnings=[ImportIssue(**w.__dict__) for w in parsed.warnings],
    )
    if dry_run or not parsed.ok:
        return result

    default_tax = next((t for t in tax_groups.values() if t.is_default), None)
    created = {"prep_areas": 0, "categories": 0, "items": 0, "variants": 0, "addon_groups": 0, "addons": 0}

    areas = {a.name.lower(): a for a in db.scalars(select(PrepArea))}
    for pi in parsed.items:
        if pi.prep_area and pi.prep_area.lower() not in areas:
            area = PrepArea(tenant_id=auth.tenant_id, name=pi.prep_area, sort_order=len(areas))
            db.add(area)
            areas[pi.prep_area.lower()] = area
            created["prep_areas"] += 1
    db.flush()
    updated = {"items": 0, "variants": 0}

    cats = {c.name.lower(): c for c in db.scalars(select(Category).where(Category.brand_id == brand_id))}
    for i, name in enumerate(parsed.categories):
        if name.lower() not in cats:
            cat = Category(tenant_id=auth.tenant_id, brand_id=brand_id, name=name, sort_order=i)
            db.add(cat)
            cats[name.lower()] = cat
            created["categories"] += 1
    db.flush()

    items = {it.name.lower(): it for it in db.scalars(select(Item).where(Item.brand_id == brand_id))}
    codes_in_use = {it.short_code: it.name.lower() for it in items.values() if it.short_code}
    for pi in parsed.items:
        if pi.short_code and codes_in_use.get(pi.short_code, pi.name.lower()) != pi.name.lower():
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Short code {pi.short_code} is already used by another item on this menu")
        tax = tax_groups.get(pi.tax_group) if pi.tax_group else default_tax
        fields = dict(category_id=cats[pi.category.lower()].id, short_code=pi.short_code, food_type=pi.food_type,
                      hsn_sac=pi.hsn_sac, description=pi.description, tax_group_id=tax.id if tax else None,
                      prep_area_id=areas[pi.prep_area.lower()].id if pi.prep_area else None)
        item = items.get(pi.name.lower())
        if item is None:
            item = Item(tenant_id=auth.tenant_id, brand_id=brand_id, name=pi.name, **fields)
            db.add(item)
            db.flush()
            items[pi.name.lower()] = item
            created["items"] += 1
        else:
            for k, v in fields.items():
                if v is not None:
                    setattr(item, k, v)
            item.is_active = True
            updated["items"] += 1
        existing = {v.name.lower(): v for v in db.scalars(select(Variant).where(Variant.item_id == item.id))}
        for order, pv in enumerate(pi.variants):
            v = existing.get(pv.name.lower())
            if v is None:
                db.add(Variant(tenant_id=auth.tenant_id, item_id=item.id, name=pv.name,
                               price_paise=pv.price_paise, sort_order=order))
                created["variants"] += 1
            else:
                v.price_paise, v.sort_order, v.is_active = pv.price_paise, order, True
                updated["variants"] += 1
    db.flush()

    groups = {g.name.lower(): g for g in db.scalars(select(AddonGroup).where(AddonGroup.brand_id == brand_id))}
    for pg in parsed.addon_groups:
        g = groups.get(pg.name.lower())
        if g is None:
            g = AddonGroup(tenant_id=auth.tenant_id, brand_id=brand_id, name=pg.name,
                           min_select=pg.min_select, max_select=pg.max_select)
            db.add(g)
            db.flush()
            created["addon_groups"] += 1
        else:
            g.min_select, g.max_select, g.is_active = pg.min_select, pg.max_select, True
        existing = {a.name.lower(): a for a in db.scalars(select(Addon).where(Addon.group_id == g.id))}
        for pa in pg.addons:
            a = existing.get(pa.name.lower())
            if a is None:
                db.add(Addon(tenant_id=auth.tenant_id, group_id=g.id, name=pa.name, price_paise=pa.price_paise))
                created["addons"] += 1
            else:
                a.price_paise, a.is_active = pa.price_paise, True
        linked = set(db.scalars(select(ItemAddonGroup.item_id).where(ItemAddonGroup.group_id == g.id)))
        for iname in pg.item_names:
            item = items[iname.lower()]
            if item.id not in linked:
                db.add(ItemAddonGroup(item_id=item.id, group_id=g.id, tenant_id=auth.tenant_id))
    db.flush()

    audit.record(db, auth.tenant_id, auth.user_id, "import", "menu", brand_id,
                 after={"created": created, "updated": updated, "file": file.filename})
    result.created = {"created": created, "updated": updated}
    return result
