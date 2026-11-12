"""Turn an order into a GST bill, and summarise shifts."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.billing import BillCalc, Discount, LineIn, compute_bill
from ..core.tax import TaxGroup as TaxCalc
from ..models import (
    Drawer,
    Bill, BillLine, CashMovement, Item, Kot, Order, OrderItem, Outlet, Payment, Shift, TaxGroup, Tenant, User, Variant,
)
from ..schemas import BillLineOut, BillOut, BillPreviewOut, DiscountIn, PaymentOut, ShiftOut

NO_TAX = TaxCalc(0, 0, 0)


@dataclass
class Row:
    order_item: OrderItem
    hsn_sac: str | None
    line: LineIn


def billable_rows(db: Session, order: Order) -> list[Row]:
    """Order lines that are still live (not on a cancelled KOT), with the tax group as it is now."""
    cancelled = set(db.scalars(select(Kot.id).where(Kot.status == "cancelled", Kot.id.in_(
        select(OrderItem.kot_id).where(OrderItem.order_id == order.id)))))
    taxes = {t.id: t for t in db.scalars(select(TaxGroup))}
    rows = []
    for oi in db.scalars(select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.created_at)):
        if oi.kot_id in cancelled:
            continue
        variant = db.get(Variant, oi.variant_id)
        item = db.get(Item, variant.item_id) if variant else None
        tg = taxes.get(item.tax_group_id) if item and item.tax_group_id else None
        tax = TaxCalc(tg.cgst_bp, tg.sgst_bp, tg.igst_bp, tg.is_inclusive) if tg else NO_TAX
        unit = oi.unit_price_paise + sum(a["price_paise"] for a in (oi.addons or []))
        rows.append(Row(oi, item.hsn_sac if item else None, LineIn(str(oi.id), oi.qty, unit, tax)))
    return rows


def to_discount(d: DiscountIn | None) -> Discount | None:
    return Discount(d.kind, d.value) if d and d.value else None


def preview(rows: list[Row], calc: BillCalc, needs: list[str]) -> BillPreviewOut:
    lines = []
    for r, ln in zip(rows, calc.lines):
        oi = r.order_item
        lines.append(BillLineOut(
            item_name=oi.item_name, variant_name=oi.variant_name,
            addons_text=", ".join(a["name"] for a in oi.addons) if oi.addons else None, hsn_sac=r.hsn_sac,
            qty=oi.qty, unit_paise=r.line.unit_paise, gross_paise=ln.gross_paise, discount_paise=ln.discount_paise,
            taxable_paise=ln.tax.taxable_paise, cgst_paise=ln.tax.cgst_paise, sgst_paise=ln.tax.sgst_paise,
            igst_paise=ln.tax.igst_paise, rate_bp=ln.rate_bp, tax_inclusive=ln.inclusive,
        ))
    return BillPreviewOut(
        lines=lines, gross_paise=calc.gross_paise, discount_paise=calc.discount_paise,
        taxable_paise=calc.taxable_paise, cgst_paise=calc.cgst_paise, sgst_paise=calc.sgst_paise,
        igst_paise=calc.igst_paise, round_off_paise=calc.round_off_paise, total_paise=calc.total_paise,
        tax_summary=calc.tax_summary(), needs_approval=needs,
    )


def approval_reasons(tenant: Tenant, calc: BillCalc, modes: list[str]) -> list[str]:
    reasons = []
    if calc.discount_paise and calc.discount_bp > tenant.discount_limit_bp:
        reasons.append(f"Discount above {tenant.discount_limit_bp / 100:g}%")
    if "complimentary" in modes:
        reasons.append("Complimentary bill")
    return reasons


def open_shift(db: Session, outlet_id: uuid.UUID, user_id: uuid.UUID) -> Shift | None:
    """This cashier's open shift at this outlet."""
    return db.scalar(select(Shift).where(Shift.outlet_id == outlet_id, Shift.opened_by == user_id,
                                         Shift.status == "open"))


def save_bill(db: Session, bill: Bill, rows: list[Row], calc: BillCalc, payments: list[tuple[str, int, str | None]]) -> None:
    db.add(bill)
    db.flush()
    for r, ln in zip(rows, calc.lines):
        oi = r.order_item
        db.add(BillLine(
            tenant_id=bill.tenant_id, bill_id=bill.id, variant_id=oi.variant_id, item_name=oi.item_name,
            variant_name=oi.variant_name, addons_text=", ".join(a["name"] for a in oi.addons) if oi.addons else None,
            hsn_sac=r.hsn_sac, qty=oi.qty, unit_paise=r.line.unit_paise, gross_paise=ln.gross_paise,
            discount_paise=ln.discount_paise, taxable_paise=ln.tax.taxable_paise, cgst_paise=ln.tax.cgst_paise,
            sgst_paise=ln.tax.sgst_paise, igst_paise=ln.tax.igst_paise, rate_bp=ln.rate_bp,
            tax_inclusive=ln.inclusive,
        ))
    for mode, amount, ref in payments:
        db.add(Payment(tenant_id=bill.tenant_id, bill_id=bill.id, mode=mode, amount_paise=amount, reference=ref))
    db.flush()
    from . import loyalty, stock   # stock used by the recipes of what was sold; points and stamps earned
    stock.resync_bill(db, bill, bill.created_by)
    loyalty.resync_bill(db, bill, bill.created_by)


def upi_link(outlet: Outlet, tenant: Tenant, amount_paise: int, invoice_no: str) -> str | None:
    if not outlet.upi_vpa or amount_paise <= 0:
        return None
    name = outlet.upi_payee_name or tenant.app_name or tenant.name
    return (f"upi://pay?pa={quote(outlet.upi_vpa)}&pn={quote(name)}&am={amount_paise / 100:.2f}"
            f"&cu=INR&tn={quote(invoice_no)}")


def bill_out(db: Session, bill: Bill) -> BillOut:
    order = db.get(Order, bill.order_id)
    outlet = db.get(Outlet, bill.outlet_id)
    tenant = db.get(Tenant, bill.tenant_id)
    lines = [BillLineOut.model_validate(ln) for ln in db.scalars(select(BillLine).where(BillLine.bill_id == bill.id))]
    summary: dict[int, dict] = {}
    for ln in lines:
        row = summary.setdefault(ln.rate_bp, {"rate_bp": ln.rate_bp, "taxable_paise": 0, "cgst_paise": 0,
                                              "sgst_paise": 0, "igst_paise": 0})
        for k in ("taxable_paise", "cgst_paise", "sgst_paise", "igst_paise"):
            row[k] += getattr(ln, k)
    cashier = db.get(User, bill.created_by)
    pays = list(db.scalars(select(Payment).where(Payment.bill_id == bill.id)))
    upi_due = sum(p.amount_paise for p in pays if p.mode == "upi")
    return BillOut(
        id=bill.id, invoice_no=bill.invoice_no, status=bill.status, order_id=bill.order_id, training=outlet.is_training,
        token_no=order.token_no, order_type=order.order_type, table_label=order.table_label,
        car_number=order.car_number, business_date=bill.business_date, created_at=bill.created_at,
        customer_name=bill.customer_name, customer_phone=bill.customer_phone, customer_gstin=bill.customer_gstin,
        discount_reason=bill.discount_reason, lines=lines, gross_paise=bill.gross_paise,
        discount_paise=bill.discount_paise, taxable_paise=bill.taxable_paise, cgst_paise=bill.cgst_paise,
        sgst_paise=bill.sgst_paise, igst_paise=bill.igst_paise, round_off_paise=bill.round_off_paise,
        total_paise=bill.total_paise, tax_summary=[summary[k] for k in sorted(summary)],
        payments=[PaymentOut.model_validate(p) for p in pays], cashier_name=cashier.name if cashier else None,
        cancel_reason=bill.cancel_reason, cancelled_at=bill.cancelled_at,
        outlet={"name": outlet.name, "legal_name": tenant.name, "address": outlet.address,
                "gstin": outlet.gstin or tenant.gstin, "fssai": outlet.fssai, "phone": outlet.phone,
                "footer": outlet.bill_footer, "logo_url": tenant.logo_url, "brand": tenant.app_name or tenant.name},
        upi_link=upi_link(outlet, tenant, upi_due, bill.invoice_no) if upi_due else None,
        revision=bill.revision, edited_at=bill.edited_at,
        locked=bool(tenant.bills_locked_through and bill.business_date <= tenant.bills_locked_through),
    )


def shift_out(db: Session, shift: Shift, blind: bool = False) -> ShiftOut:
    bill_ids = list(db.scalars(select(Bill.id).where(Bill.shift_id == shift.id, Bill.status == "paid")))
    by_mode: dict[str, int] = {}
    if bill_ids:
        for mode, amount in db.execute(
            select(Payment.mode, func.sum(Payment.amount_paise)).where(Payment.bill_id.in_(bill_ids))
            .group_by(Payment.mode)
        ):
            by_mode[mode] = int(amount or 0)
    moves = list(db.scalars(select(CashMovement).where(CashMovement.shift_id == shift.id)))
    cash_in = sum(m.amount_paise for m in moves if m.kind == "in")
    cash_out = sum(m.amount_paise for m in moves if m.kind == "out")
    cash_sales = by_mode.get("cash", 0)
    expected = shift.opening_cash_paise + cash_sales + cash_in - cash_out
    sales = db.scalar(select(func.coalesce(func.sum(Bill.total_paise), 0)).where(
        Bill.shift_id == shift.id, Bill.status == "paid")) or 0
    opener = db.get(User, shift.opened_by)
    counted = shift.counted_cash_paise
    out = ShiftOut(
        id=shift.id, outlet_id=shift.outlet_id, status=shift.status, opened_at=shift.opened_at,
        opened_by_name=opener.name if opener else None, opening_cash_paise=shift.opening_cash_paise,
        opening_denoms=shift.opening_denoms, counted_denoms=shift.counted_denoms,
        closed_at=shift.closed_at, cash_sales_paise=cash_sales, cash_in_paise=cash_in, cash_out_paise=cash_out,
        expected_cash_paise=expected if shift.status == "open" else (shift.expected_cash_paise or expected),
        counted_cash_paise=counted, variance_paise=None if counted is None else counted - (shift.expected_cash_paise or expected),
        bills=len(bill_ids), sales_paise=int(sales), by_mode=by_mode,
        expected_opening_paise=shift.expected_opening_paise,
        drawer_id=shift.drawer_id, drawer_name=(db.get(Drawer, shift.drawer_id).name if shift.drawer_id and db.get(Drawer, shift.drawer_id) else None),
        closed_by_name=(db.get(User, shift.closed_by).name if shift.closed_by and db.get(User, shift.closed_by) else None),
        opened_by_id=shift.opened_by,
        movements=[{"kind": m.kind, "amount_paise": m.amount_paise, "denoms": m.denoms, "reason": m.reason,
                    "at": m.created_at.isoformat() if m.created_at else None,
                    "by": (db.get(User, m.created_by).name if db.get(User, m.created_by) else None)}
                   for m in sorted(moves, key=lambda x: x.created_at or shift.opened_at)],
    )
    if blind and shift.status == "open":
        out.expected_cash_paise = None
        out.cash_sales_paise = None
        out.expected_opening_paise = None
    return out


def fy_counter_date(fy_start: int, start_month: int) -> date:
    """Invoice sequences reuse the daily counter table, keyed on the FY's first day."""
    return date(fy_start, start_month, 1)


def quote_lines(db: Session, resolved, discount_paise: int = 0) -> BillCalc:
    """GST-inclusive total for lines that aren't an order yet (kiosk checkout)."""
    taxes = {t.id: t for t in db.scalars(select(TaxGroup))}
    lines = []
    for i, r in enumerate(resolved):
        tg = taxes.get(r.item.tax_group_id) if r.item.tax_group_id else None
        tax = TaxCalc(tg.cgst_bp, tg.sgst_bp, tg.igst_bp, tg.is_inclusive) if tg else NO_TAX
        unit = r.price_paise + sum(a["price_paise"] for a in r.addons)
        lines.append(LineIn(str(i), r.line.qty, unit, tax))
    return compute_bill(lines, Discount("flat", discount_paise) if discount_paise else None)
