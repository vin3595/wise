"""Stock on hand: every change is a StockMove and updates StockLevel in the same transaction.

Sales: a bill's stock use comes from its lines' recipes (item size + add-ons). `resync_bill` posts only the
difference between what the bill should have used and what was already taken for it, so it is right after a
new bill, a cancellation (gives it all back), a correction, or a retry. Stock may go below zero (selling is
never blocked); that shows up as negative stock to count and fix.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.kds import business_date
from ..core.stock import Use, consumption, explode, value_paise, weighted_cost
from ..models import Addon, AddonGroup, Bill, BillLine, Outlet, RecipeLine, StockItem, StockLevel, StockMove

INCOMING = {"purchase", "prep_in"}


def level(db: Session, tenant_id: uuid.UUID, outlet_id: uuid.UUID, item_id: uuid.UUID, lock: bool = True) -> StockLevel:
    stmt = select(StockLevel).where(StockLevel.outlet_id == outlet_id, StockLevel.stock_item_id == item_id)
    lv = db.scalar(stmt.with_for_update() if lock else stmt)
    if lv is None:
        lv = StockLevel(tenant_id=tenant_id, outlet_id=outlet_id, stock_item_id=item_id, qty_m=0, cost_k=0)
        db.add(lv)
        db.flush()
    return lv


def post(db: Session, *, tenant_id: uuid.UUID, outlet_id: uuid.UUID, item: StockItem, qty_m: int, kind: str,
         ref_kind: str | None = None, ref_id: uuid.UUID | None = None, note: str | None = None,
         user_id: uuid.UUID | None = None, day: date | None = None, in_cost_k: int | None = None) -> StockMove:
    """One stock change. Incoming stock (purchase, prep) carries its own cost and moves the average;
    everything else is valued at the current average (or the last purchase price if there is none yet)."""
    lv = level(db, tenant_id, outlet_id, item.id)
    if kind in INCOMING and qty_m > 0:
        cost = in_cost_k if in_cost_k is not None else (lv.cost_k or item.last_cost_k)
        lv.cost_k = weighted_cost(lv.qty_m, lv.cost_k, qty_m, cost)
    else:
        cost = lv.cost_k or item.last_cost_k
    lv.qty_m += qty_m
    mv = StockMove(tenant_id=tenant_id, outlet_id=outlet_id, stock_item_id=item.id, business_date=day or business_date(),
                   kind=kind, qty_m=qty_m, cost_k=cost, value_paise=value_paise(qty_m, cost), ref_kind=ref_kind,
                   ref_id=ref_id, note=note, created_by=user_id)
    db.add(mv)
    return mv


def recipes(db: Session, owner_kind: str, owner_ids) -> dict[uuid.UUID, list[Use]]:
    out: dict[uuid.UUID, list[Use]] = defaultdict(list)
    ids = list(owner_ids)
    if not ids:
        return out
    for r in db.scalars(select(RecipeLine).where(RecipeLine.owner_kind == owner_kind, RecipeLine.owner_id.in_(ids))):
        out[r.owner_id].append(Use(str(r.stock_item_id), r.qty_m))
    return out


def prep_book(db: Session) -> tuple[dict[str, list[Use]], dict[str, int], set[str]]:
    """Every prep item's batch recipe and batch size, and which are made fresh when sold."""
    items = list(db.scalars(select(StockItem).where(StockItem.kind == "prep")))
    rec = recipes(db, "prep", [i.id for i in items])
    return ({str(k): v for k, v in rec.items()}, {str(i.id): i.yield_m for i in items if i.yield_m},
            {str(i.id) for i in items if i.made_on_sale})


def addon_ids_by_name(db: Session, brand_id: uuid.UUID) -> dict[str, uuid.UUID]:
    rows = db.execute(select(Addon.name, Addon.id).join(AddonGroup, AddonGroup.id == Addon.group_id)
                      .where(AddonGroup.brand_id == brand_id))
    return {n.strip().lower(): i for n, i in rows}


def bill_usage(db: Session, bill: Bill) -> dict[str, int]:
    """Stock this bill should have used (nothing once it's cancelled)."""
    if bill.status != "paid":
        return {}
    lines = list(db.scalars(select(BillLine).where(BillLine.bill_id == bill.id)))
    if not lines:
        return {}
    by_variant = recipes(db, "variant", {ln.variant_id for ln in lines if ln.variant_id})
    names = {n.strip().lower() for ln in lines if ln.addons_text for n in ln.addons_text.split(",")}
    addon_rec: dict[uuid.UUID, list[Use]] = {}
    addon_ids: dict[str, uuid.UUID] = {}
    if names:
        outlet = db.get(Outlet, bill.outlet_id)
        addon_ids = addon_ids_by_name(db, outlet.brand_id)
        addon_rec = recipes(db, "addon", [addon_ids[n] for n in names if n in addon_ids])
    parts = []
    for ln in lines:
        rec = list(by_variant.get(ln.variant_id, [])) if ln.variant_id else []
        for n in (ln.addons_text.split(",") if ln.addons_text else []):
            aid = addon_ids.get(n.strip().lower())
            if aid:
                rec += addon_rec.get(aid, [])
        if rec:
            parts.append((ln.qty, rec))
    used = consumption(parts)
    book, yields, on_sale = prep_book(db)
    return explode(used, book, yields, on_sale) if on_sale & set(used) else used


def resync_bill(db: Session, bill: Bill, user_id: uuid.UUID | None = None) -> None:
    db.flush()
    want = bill_usage(db, bill)
    taken: dict[str, int] = {}
    for item_id, q in db.execute(select(StockMove.stock_item_id, func.sum(StockMove.qty_m))
                                 .where(StockMove.ref_kind == "bill", StockMove.ref_id == bill.id)
                                 .group_by(StockMove.stock_item_id)):
        taken[str(item_id)] = -int(q)
    for item_id in sorted(set(want) | set(taken)):
        diff = want.get(item_id, 0) - taken.get(item_id, 0)
        if diff == 0:
            continue
        item = db.get(StockItem, uuid.UUID(item_id))
        if item is None:
            continue
        post(db, tenant_id=bill.tenant_id, outlet_id=bill.outlet_id, item=item, qty_m=-diff, kind="sale",
             ref_kind="bill", ref_id=bill.id, user_id=user_id, day=bill.business_date,
             note=None if not taken else ("cancelled" if bill.status != "paid" else "bill corrected"))
    db.flush()
