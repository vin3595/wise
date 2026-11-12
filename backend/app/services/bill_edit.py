"""Correcting issued bills, one or many at a time.

Every bill in a batch is checked first without touching anything; only when all of them are fine is
anything written, so a batch is saved completely or not at all. Each corrected bill keeps its invoice
number, its previous version is stored as a BillRevision, and its sales/GST/item reports follow the
corrected lines (reports read BillLine). The kitchen side (orders, KOTs) is not changed.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.bill_edit import EditFacts, approval_needed, blockers, describe
from ..core.billing import BillCalc, Discount, LineIn, PaymentIn, compute_bill, validate_payments
from ..core.kds import business_date
from ..core.tax import TaxGroup as TaxCalc
from ..models import Bill, BillLine, BillRevision, Outlet, Payment, Shift, TaxGroup, Tenant
from ..schemas import BillFix, BillFixResult, BillLineOut, BillPreviewOut
from .billing import approval_reasons, bill_out
from .orders import resolve_lines


class FixError(Exception):
    pass


@dataclass
class Line:
    variant_id: uuid.UUID
    item_name: str
    variant_name: str
    addons_text: str | None
    hsn_sac: str | None
    qty: int
    unit_paise: int
    tax: TaxCalc


@dataclass
class Plan:
    bill: Bill
    lines: list[Line] | None            # None = lines unchanged
    calc: BillCalc | None
    discount_reason: str | None
    payments: list[tuple[str, int, str | None]] | None
    customer: dict | None
    result: BillFixResult
    approvals: list[str] = field(default_factory=list)


def _tax_of(ln: BillLine) -> TaxCalc:
    """A bill line keeps the GST rate it was billed at (the item's tax group may have changed since)."""
    if ln.igst_paise:
        return TaxCalc(0, 0, ln.rate_bp, ln.tax_inclusive)
    return TaxCalc(ln.rate_bp // 2, ln.rate_bp - ln.rate_bp // 2, 0, ln.tax_inclusive)


def _preview(lines: list[Line], calc: BillCalc) -> BillPreviewOut:
    return BillPreviewOut(
        lines=[BillLineOut(item_name=l.item_name, variant_name=l.variant_name, addons_text=l.addons_text,
                           hsn_sac=l.hsn_sac, qty=l.qty, unit_paise=l.unit_paise, gross_paise=c.gross_paise,
                           discount_paise=c.discount_paise, taxable_paise=c.tax.taxable_paise,
                           cgst_paise=c.tax.cgst_paise, sgst_paise=c.tax.sgst_paise, igst_paise=c.tax.igst_paise,
                           rate_bp=c.rate_bp, tax_inclusive=c.inclusive) for l, c in zip(lines, calc.lines)],
        gross_paise=calc.gross_paise, discount_paise=calc.discount_paise, taxable_paise=calc.taxable_paise,
        cgst_paise=calc.cgst_paise, sgst_paise=calc.sgst_paise, igst_paise=calc.igst_paise,
        round_off_paise=calc.round_off_paise, total_paise=calc.total_paise, tax_summary=calc.tax_summary(),
        needs_approval=[])


def plan_fix(db: Session, tenant: Tenant, fix: BillFix, *, is_manager: bool, user_id: uuid.UUID) -> Plan:
    """Works out one bill's correction without changing anything. Raises FixError with a message
    the cashier can act on."""
    bill = db.get(Bill, fix.bill_id)
    if bill is None:
        raise FixError("Bill not found")
    if bill.status != "paid":
        raise FixError("This bill is cancelled")
    if bill.revision != fix.revision:
        raise FixError("Someone else corrected this bill meanwhile. Reload and try again.")
    outlet = db.get(Outlet, bill.outlet_id)
    old_lines = list(db.scalars(select(BillLine).where(BillLine.bill_id == bill.id)))
    by_id = {l.id: l for l in old_lines}
    for e in fix.lines:
        if e.line_id not in by_id:
            raise FixError("A line isn't on this bill")

    qty = {e.line_id: e.qty for e in fix.lines}
    items_changed = any(qty[l.id] != l.qty for l in old_lines if l.id in qty) or bool(fix.add_items)
    lines = [Line(l.variant_id, l.item_name, l.variant_name, l.addons_text, l.hsn_sac, qty.get(l.id, l.qty),
                  l.unit_paise, _tax_of(l)) for l in old_lines]
    lines = [l for l in lines if l.qty > 0]
    if fix.add_items:
        taxes = {t.id: t for t in db.scalars(select(TaxGroup))}
        try:
            resolved = resolve_lines(db, outlet, fix.add_items)
        except HTTPException as e:
            raise FixError(str(e.detail)) from None
        for r in resolved:
            tg = taxes.get(r.item.tax_group_id) if r.item.tax_group_id else None
            tax = TaxCalc(tg.cgst_bp, tg.sgst_bp, tg.igst_bp, tg.is_inclusive) if tg else TaxCalc(0, 0, 0)
            lines.append(Line(r.variant.id, r.item.name, r.variant.name,
                              ", ".join(a["name"] for a in r.addons) or None, r.item.hsn_sac, r.line.qty,
                              r.price_paise + sum(a["price_paise"] for a in r.addons), tax))
    if not lines:
        raise FixError("A bill needs at least one item. To void the whole bill, cancel it.")

    # Discount: sent = set (0 removes); left out = keep the same rupee amount.
    discount_reason = bill.discount_reason
    if fix.discount is not None:
        disc = Discount(fix.discount.kind, fix.discount.value) if fix.discount.value else None
        discount_reason = (fix.discount.reason or "").strip() or None
    else:
        disc = Discount("flat", bill.discount_paise) if bill.discount_paise else None
    calc = None
    if items_changed or fix.discount is not None:
        try:
            calc = compute_bill([LineIn(str(i), l.qty, l.unit_paise, l.tax) for i, l in enumerate(lines)], disc)
        except ValueError as e:
            raise FixError(str(e)) from None
        if calc.discount_paise != bill.discount_paise:
            items_changed = True
        if calc.discount_paise and not discount_reason:
            raise FixError("Give a reason for the discount")
        if not items_changed and calc.total_paise == bill.total_paise:
            calc = None
    new_total = calc.total_paise if calc else bill.total_paise

    old_pays = list(db.scalars(select(Payment).where(Payment.bill_id == bill.id)))
    payments = None
    if fix.payments is not None:
        payments = [(p.mode, p.amount_paise, p.reference) for p in fix.payments]
        if sorted((m, a) for m, a, _ in payments) == sorted((p.mode, p.amount_paise) for p in old_pays):
            payments = None
    if fix.payment_mode is not None and fix.payments is None:
        if len(old_pays) != 1 or old_pays[0].mode in ("due", "complimentary"):
            raise FixError("This bill wasn't paid one way; correct the payment split instead")
        if old_pays[0].mode != fix.payment_mode or new_total != bill.total_paise:
            payments = [(fix.payment_mode, new_total, None)]
    if payments is None and new_total != bill.total_paise:
        if len(old_pays) == 1 and old_pays[0].mode not in ("due", "complimentary"):
            payments = [(old_pays[0].mode, new_total, old_pays[0].reference)]  # one payment: follows the total
        else:
            raise FixError(f"The total is now ₹{new_total / 100:,.2f}. Update the payment split.")
    if payments is not None:
        try:
            validate_payments(new_total, [PaymentIn(m, a) for m, a, _ in payments])
        except ValueError as e:
            raise FixError(str(e)) from None

    customer = None
    if fix.customer is not None:
        c = fix.customer
        new = {"customer_name": (c.name or "").strip() or None, "customer_phone": c.phone,
               "customer_declined_phone": c.declined and not c.phone, "customer_gstin": c.gstin}
        if not c.phone and not c.declined:
            raise FixError("Enter the customer's mobile number, or mark it declined")
        if any(getattr(bill, k) != v for k, v in new.items()):
            customer = new
    modes = [m for m, _, _ in payments] if payments is not None else [p.mode for p in old_pays]
    if "due" in modes and not ((customer or {}).get("customer_phone") or (customer is None and bill.customer_phone)):
        raise FixError("A credit (due) bill needs the customer's mobile number")

    shift = db.get(Shift, bill.shift_id)
    facts = EditFacts(items_changed=items_changed, payments_changed=payments is not None,
                      customer_changed=customer is not None, old_total_paise=bill.total_paise, new_total_paise=new_total,
                      bill_date=bill.business_date, today=business_date(), shift_open=bool(shift and shift.status == "open"),
                      is_manager=is_manager, locked_through=tenant.bills_locked_through)
    if (why := blockers(facts)) is not None:
        raise FixError(why)
    if not is_manager and shift is not None and shift.opened_by != user_id and (items_changed or payments is not None):
        raise FixError("This bill is on another cashier's shift; a manager can correct it")
    approvals = approval_needed(facts)
    if calc is not None:
        approvals += [r for r in approval_reasons(tenant, calc, modes) if r not in approvals]
    elif "complimentary" in modes and payments is not None:
        approvals.append("Complimentary bill")

    shown = calc if calc is not None else None
    result = BillFixResult(
        bill_id=bill.id, invoice_no=bill.invoice_no, ok=True, changes=describe(facts),
        needs_approval=[] if is_manager else approvals, old_total_paise=bill.total_paise, new_total_paise=new_total,
        preview=_preview(lines, shown) if shown is not None else None)
    return Plan(bill, lines if calc is not None else None, calc, discount_reason, payments, customer, result, approvals)


def apply_plan(db: Session, p: Plan, *, user_id: uuid.UUID, approver: uuid.UUID | None, reason: str) -> None:
    bill = p.bill
    before_total = bill.total_paise
    db.add(BillRevision(tenant_id=bill.tenant_id, bill_id=bill.id, revision_no=bill.revision,
                        snapshot=bill_out(db, bill).model_dump(mode="json"), old_total_paise=bill.total_paise,
                        new_total_paise=p.result.new_total_paise, changes=p.result.changes, reason=reason,
                        edited_by=user_id, approved_by=approver))
    if p.calc is not None and p.lines is not None:
        c = p.calc
        db.execute(delete(BillLine).where(BillLine.bill_id == bill.id))
        for l, ln in zip(p.lines, c.lines):
            db.add(BillLine(tenant_id=bill.tenant_id, bill_id=bill.id, variant_id=l.variant_id, item_name=l.item_name,
                            variant_name=l.variant_name, addons_text=l.addons_text, hsn_sac=l.hsn_sac, qty=l.qty,
                            unit_paise=l.unit_paise, gross_paise=ln.gross_paise, discount_paise=ln.discount_paise,
                            taxable_paise=ln.tax.taxable_paise, cgst_paise=ln.tax.cgst_paise,
                            sgst_paise=ln.tax.sgst_paise, igst_paise=ln.tax.igst_paise, rate_bp=ln.rate_bp,
                            tax_inclusive=ln.inclusive))
        bill.gross_paise, bill.discount_paise, bill.taxable_paise = c.gross_paise, c.discount_paise, c.taxable_paise
        bill.cgst_paise, bill.sgst_paise, bill.igst_paise = c.cgst_paise, c.sgst_paise, c.igst_paise
        bill.round_off_paise, bill.total_paise = c.round_off_paise, c.total_paise
        bill.discount_reason = p.discount_reason if c.discount_paise else None
    if p.payments is not None:
        db.execute(delete(Payment).where(Payment.bill_id == bill.id))
        for mode, amount, ref in p.payments:
            db.add(Payment(tenant_id=bill.tenant_id, bill_id=bill.id, mode=mode, amount_paise=amount, reference=ref))
    if p.customer is not None:
        for k, v in p.customer.items():
            setattr(bill, k, v)
    if approver is not None and p.approvals:
        bill.approved_by = approver
    bill.revision += 1
    bill.edited_at = datetime.now(timezone.utc)
    db.flush()
    from . import loyalty, stock
    stock.resync_bill(db, bill, user_id)
    loyalty.resync_bill(db, bill, user_id)
    from .alerts import on_bill_corrected
    on_bill_corrected(db, bill, before_total, user_id, reason)
