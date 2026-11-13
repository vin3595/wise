"""Files for the accountant: Tally Prime import (XML) and an Excel workbook of registers.

Tally: masters (ledgers, import once), day-wise sales vouchers, and purchase vouchers per supplier bill.
Excel: sales register (every bill), GST by rate, HSN summary, day-wise money, purchase register.
"""
from __future__ import annotations

import io
import uuid
from collections import defaultdict
from datetime import date

from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import tally
from ..core.permissions import Perm
from ..deps import Auth, get_db, require
from ..models import AccountsSettings, Bill, BillLine, Outlet, Payment, Purchase, Supplier, Tenant
from .reports import _outlets, _range

IST = ZoneInfo("Asia/Kolkata")
router = APIRouter(tags=["accounts"])
MODES = ("cash", "upi", "card", "due", "complimentary")
MODE_LABEL = {"cash": "Cash", "upi": "UPI", "card": "Card", "due": "Credit", "complimentary": "Complimentary"}


class AccountsIn(BaseModel):
    company_name: str | None = Field(default=None, max_length=120)
    ledgers: dict[str, str] = {}


def _cfg(db: Session, tenant_id: uuid.UUID) -> AccountsSettings:
    cfg = db.get(AccountsSettings, tenant_id)
    if cfg is None:
        cfg = AccountsSettings(tenant_id=tenant_id, ledgers={})
        db.add(cfg)
        db.flush()
    return cfg


@router.get("/tenant/accounts")
def get_accounts(auth: Auth = Depends(require(Perm.reports_view)), db: Session = Depends(get_db)) -> dict:
    cfg = _cfg(db, auth.tenant_id)
    tenant = db.get(Tenant, auth.tenant_id)
    return {"company_name": cfg.company_name or tenant.name, "ledgers": {**tally.DEFAULT_LEDGERS, **(cfg.ledgers or {})},
            "defaults": tally.DEFAULT_LEDGERS}


@router.put("/tenant/accounts")
def put_accounts(body: AccountsIn, auth: Auth = Depends(require(Perm.tenant_settings)), db: Session = Depends(get_db)) -> dict:
    cfg = _cfg(db, auth.tenant_id)
    cfg.company_name = (body.company_name or "").strip() or None
    cfg.ledgers = {k: v.strip() for k, v in body.ledgers.items() if k in tally.DEFAULT_LEDGERS and v.strip() and v.strip() != tally.DEFAULT_LEDGERS[k]}
    db.flush()
    return get_accounts(auth, db)


# ------------------------------------------------------------------ data

def _day_sales(db: Session, outlets: list[uuid.UUID], d1: date, d2: date) -> list[tally.DaySales]:
    paid = [Bill.outlet_id.in_(outlets), Bill.business_date >= d1, Bill.business_date <= d2, Bill.status == "paid"]
    names = {o.id: o for o in db.scalars(select(Outlet).where(Outlet.id.in_(outlets)))}
    days: dict[tuple, dict] = {}
    for oid, d, n, first, last, ro in db.execute(select(Bill.outlet_id, Bill.business_date, func.count(), func.min(Bill.invoice_no),
                                                        func.max(Bill.invoice_no), func.sum(Bill.round_off_paise)).where(*paid)
                                                 .group_by(Bill.outlet_id, Bill.business_date)):
        days[(oid, d)] = {"n": n, "first": first, "last": last, "ro": int(ro), "rate": {}, "mode": {}}
    for oid, d, rate, tx, c, s, i in db.execute(select(Bill.outlet_id, Bill.business_date, BillLine.rate_bp, func.sum(BillLine.taxable_paise),
                                                       func.sum(BillLine.cgst_paise), func.sum(BillLine.sgst_paise), func.sum(BillLine.igst_paise))
                                                .join(Bill, Bill.id == BillLine.bill_id).where(*paid)
                                                .group_by(Bill.outlet_id, Bill.business_date, BillLine.rate_bp)):
        days[(oid, d)]["rate"][int(rate)] = (int(tx), int(c), int(s), int(i))
    for oid, d, mode, a in db.execute(select(Bill.outlet_id, Bill.business_date, Payment.mode, func.sum(Payment.amount_paise))
                                      .join(Bill, Bill.id == Payment.bill_id).where(*paid).group_by(Bill.outlet_id, Bill.business_date, Payment.mode)):
        days[(oid, d)]["mode"][mode] = int(a)
    out = []
    for (oid, d), x in sorted(days.items(), key=lambda kv: (kv[0][1], names[kv[0][0]].name)):
        o = names[oid]
        out.append(tally.DaySales(d, o.name, o.code, x["n"], x["first"], x["last"], x["rate"], x["mode"], x["ro"]))
    return out


def _supplier_bills(db: Session, outlets: list[uuid.UUID], d1: date, d2: date) -> list[tuple[Purchase, Supplier | None]]:
    sups = {s.id: s for s in db.scalars(select(Supplier))}
    return [(p, sups.get(p.supplier_id) if p.supplier_id else None)
            for p in db.scalars(select(Purchase).where(Purchase.outlet_id.in_(outlets), Purchase.invoice_date >= d1, Purchase.invoice_date <= d2,
                                                       Purchase.status == "posted").order_by(Purchase.invoice_date))]


def _attach(content: bytes, name: str, mime: str) -> Response:
    return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ Tally

@router.get("/accounts/tally")
def tally_file(kind: str = Query(pattern=r"^(masters|sales|purchases)$"), outlet_id: uuid.UUID | None = None,
               date_from: date | None = None, date_to: date | None = None,
               auth: Auth = Depends(require(Perm.reports_view)), db: Session = Depends(get_db)) -> Response:
    outlets = _outlets(db, auth, outlet_id, allow_training=False)
    d1, d2 = _range(date_from, date_to)
    s = get_accounts(auth, db)
    ledgers, company = s["ledgers"], s["company_name"]
    span = f"{d1}" + ("" if d1 == d2 else f"-to-{d2}")
    if kind == "masters":
        days = _day_sales(db, outlets, d1, d2)
        rates = sorted({r for d in days for r in d.by_rate} | {500})
        prates = sorted({ln["gst_bp"] for p, _ in _supplier_bills(db, outlets, d1, d2) for ln in p.lines} | {500, 1800})
        names: list[tuple[str, str, str | None]] = []
        for key in ("cash", "upi", "card", "due", "complimentary", "cgst", "sgst", "igst", "round_off", "in_cgst", "in_sgst"):
            names.append((tally.ledger_name(ledgers, key), tally.PARENTS[key], tally.DUTY_HEAD.get(key)))
        names += [(tally.ledger_name(ledgers, "sales", r), "Sales Accounts", None) for r in rates]
        names += [(tally.ledger_name(ledgers, "purchase", r), "Purchase Accounts", None) for r in prates]
        sups = [(x.name, x.gstin) for x in db.scalars(select(Supplier).where(Supplier.is_active.is_(True)))]
        xml = tally.envelope(company, tally.ledger_masters(names, sups), report="All Masters")
        return _attach(xml.encode(), "wise-tally-ledgers.xml", "application/xml")
    if kind == "sales":
        vouchers = [tally.sales_voucher(d, ledgers).xml() for d in _day_sales(db, outlets, d1, d2)]
        if not vouchers:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No bills in these dates")
        return _attach(tally.envelope(company, vouchers).encode(), f"wise-tally-sales-{span}.xml", "application/xml")
    bills = _supplier_bills(db, outlets, d1, d2)
    if not bills:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No purchases entered in these dates")
    vouchers = [tally.purchase_voucher(tally.SupplierBill(p.invoice_date, p.invoice_no or f"P-{str(p.id)[:8]}", sup.name if sup else None,
                                                          [(ln["gst_bp"], ln["amount_paise"], ln["tax_paise"]) for ln in p.lines]), ledgers).xml()
                for p, sup in bills]
    return _attach(tally.envelope(company, vouchers).encode(), f"wise-tally-purchases-{span}.xml", "application/xml")


# ------------------------------------------------------------------ Excel

@router.get("/accounts/excel")
def excel_file(outlet_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
               auth: Auth = Depends(require(Perm.reports_view)), db: Session = Depends(get_db)) -> Response:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    outlets = _outlets(db, auth, outlet_id, allow_training=False)
    d1, d2 = _range(date_from, date_to)
    tenant = db.get(Tenant, auth.tenant_id)
    onames = {o.id: o.name for o in db.scalars(select(Outlet).where(Outlet.id.in_(outlets)))}
    wb = Workbook()
    head_font, head_fill, total_font = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="2D2640"), Font(bold=True)
    money = "#,##0.00"

    def sheet(title: str, cols: list[tuple[str, int, bool]], rows: list[list], note: str):
        ws = wb.create_sheet(title)
        ws.append([f"{tenant.name}: {title}, {d1:%d %b %Y}" + ("" if d1 == d2 else f" to {d2:%d %b %Y}")])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append([note])
        ws["A2"].font = Font(italic=True, color="666666")
        ws.append([c[0] for c in cols])
        for i, (_, w, _m) in enumerate(cols, 1):
            cell = ws.cell(row=3, column=i)
            cell.font, cell.fill, cell.alignment = head_font, head_fill, Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[get_column_letter(i)].width = w
        for r in rows:
            ws.append(r)
        last = 3 + len(rows)
        if rows:
            ws.append(["Total"] + [f"=SUM({get_column_letter(i)}4:{get_column_letter(i)}{last})" if m else None for i, (_, _w, m) in enumerate(cols, 1)][1:])
            for i, (_, _w, m) in enumerate(cols, 1):
                ws.cell(row=last + 1, column=i).font = total_font
        for i, (_, _w, m) in enumerate(cols, 1):
            if m:
                for row in range(4, last + 2):
                    ws.cell(row=row, column=i).number_format = money
        ws.freeze_panes = "A4"
        return ws

    r = lambda p: round((p or 0) / 100, 2)  # noqa: E731
    # Sales register
    bills = list(db.scalars(select(Bill).where(Bill.outlet_id.in_(outlets), Bill.business_date >= d1, Bill.business_date <= d2)
                            .order_by(Bill.business_date, Bill.created_at)))
    pays: dict = defaultdict(lambda: defaultdict(int))
    for bid, mode, a in db.execute(select(Payment.bill_id, Payment.mode, func.sum(Payment.amount_paise)).where(Payment.bill_id.in_([b.id for b in bills]))
                                   .group_by(Payment.bill_id, Payment.mode)) if bills else []:
        pays[bid][mode] = int(a)
    rows = []
    for b in bills:
        ok = b.status == "paid"
        z = (lambda p: r(p) if ok else 0)  # noqa: E731
        rows.append([str(b.business_date), b.invoice_no, "Paid" if ok else f"Cancelled: {b.cancel_reason or ''}", onames.get(b.outlet_id, ""),
                     b.customer_name or "", b.customer_phone or "", b.customer_gstin or "", z(b.gross_paise), z(b.discount_paise), z(b.taxable_paise),
                     z(b.cgst_paise), z(b.sgst_paise), z(b.igst_paise), z(b.round_off_paise), z(b.total_paise)] + [z(pays[b.id].get(m, 0)) for m in MODES])
    sheet("Sales register", [("Date", 11, False), ("Bill no.", 18, False), ("Status", 12, False), ("Outlet", 14, False), ("Customer", 16, False),
                             ("Mobile", 12, False), ("GSTIN", 17, False), ("Gross", 11, True), ("Discount", 10, True), ("Taxable", 11, True),
                             ("CGST", 9, True), ("SGST", 9, True), ("IGST", 9, True), ("Round off", 9, True), ("Total", 11, True)]
          + [(MODE_LABEL[m], 11, True) for m in MODES], rows,
          "Every bill. Cancelled bills stay listed (their number is used) with zero amounts. Corrected bills show their corrected amounts.")
    # GST by rate and HSN
    paid = [Bill.outlet_id.in_(outlets), Bill.business_date >= d1, Bill.business_date <= d2, Bill.status == "paid"]
    rate_rows = [[f"{int(rt) / 100:g}%", r(tx), r(c), r(s), r(i), r(int(c) + int(s) + int(i))] for rt, tx, c, s, i in db.execute(
        select(BillLine.rate_bp, func.sum(BillLine.taxable_paise), func.sum(BillLine.cgst_paise), func.sum(BillLine.sgst_paise), func.sum(BillLine.igst_paise))
        .join(Bill, Bill.id == BillLine.bill_id).where(*paid).group_by(BillLine.rate_bp).order_by(BillLine.rate_bp))]
    sheet("GST by rate", [("GST rate", 10, False), ("Taxable", 14, True), ("CGST", 12, True), ("SGST", 12, True), ("IGST", 12, True), ("Total GST", 12, True)],
          rate_rows, "For GSTR-1 / GSTR-3B: sales after discounts, by GST rate.")
    hsn_rows = [[h or "(none)", f"{int(rt) / 100:g}%", int(q), r(tx), r(c), r(s), r(i)] for h, rt, q, tx, c, s, i in db.execute(
        select(BillLine.hsn_sac, BillLine.rate_bp, func.sum(BillLine.qty), func.sum(BillLine.taxable_paise), func.sum(BillLine.cgst_paise),
               func.sum(BillLine.sgst_paise), func.sum(BillLine.igst_paise)).join(Bill, Bill.id == BillLine.bill_id).where(*paid)
        .group_by(BillLine.hsn_sac, BillLine.rate_bp).order_by(BillLine.hsn_sac))]
    sheet("HSN summary", [("HSN / SAC", 12, False), ("Rate", 8, False), ("Quantity", 10, False), ("Taxable", 14, True), ("CGST", 12, True),
                          ("SGST", 12, True), ("IGST", 12, True)], hsn_rows, "HSN-wise summary table of GSTR-1.")
    day_rows = [[str(d.day), d.outlet, d.bills, r(sum(v[0] for v in d.by_rate.values())), r(sum(v[1] + v[2] + v[3] for v in d.by_rate.values())),
                 r(d.round_off), r(sum(d.by_mode.values()))] + [r(d.by_mode.get(m, 0)) for m in MODES] for d in _day_sales(db, outlets, d1, d2)]
    sheet("Day-wise", [("Date", 11, False), ("Outlet", 14, False), ("Bills", 7, False), ("Taxable", 12, True), ("GST", 11, True), ("Round off", 9, True),
                       ("Total", 12, True)] + [(MODE_LABEL[m], 11, True) for m in MODES], day_rows,
          "Same figures as the Tally sales vouchers, one line per outlet per day.")
    prow = []
    for p, sup in _supplier_bills(db, outlets, d1, d2):
        prow.append([str(p.invoice_date), sup.name if sup else "", (sup.gstin if sup else "") or "", p.invoice_no or "", onames.get(p.outlet_id, ""),
                     len(p.lines), r(p.amount_paise), r(p.tax_paise), r(p.total_paise)])
    sheet("Purchase register", [("Bill date", 11, False), ("Supplier", 20, False), ("Supplier GSTIN", 17, False), ("Bill no.", 14, False),
                                ("Outlet", 14, False), ("Items", 7, False), ("Before GST", 12, True), ("GST", 11, True), ("Total", 12, True)], prow,
          "Supplier bills entered under Stock > Purchases. Check input GST against GSTR-2B.")
    from ..core.cash_purpose import type_label
    from ..models import CashMovement, Drawer, Shift, User
    unames = {u.id: u.name for u in db.scalars(select(User))}
    dnames = {d.id: d.name for d in db.scalars(select(Drawer))}
    cday = func.date(func.timezone("Asia/Kolkata", CashMovement.created_at))
    crow = []
    for m, oid, did in db.execute(select(CashMovement, Shift.outlet_id, Shift.drawer_id).join(Shift, Shift.id == CashMovement.shift_id)
                                  .where(Shift.outlet_id.in_(outlets), cday >= d1, cday <= d2).order_by(CashMovement.created_at)):
        crow.append([m.created_at.astimezone(IST).strftime("%Y-%m-%d %H:%M"), onames.get(oid, ""), dnames.get(did, ""), unames.get(m.created_by, ""),
                     type_label(m.kind, m.purpose, m.handed), (m.category if m.purpose == "expense" else m.paid_to) or "",
                     (m.note or "") if m.purpose else m.reason, r(m.amount_paise) if m.kind == "out" else 0, r(m.amount_paise) if m.kind == "in" else 0])
    sheet("Cash expenses & drawings", [("When", 16, False), ("Outlet", 14, False), ("Drawer", 10, False), ("Cashier", 14, False), ("Type", 26, False),
                                       ("Category / given to", 20, False), ("Note", 24, False), ("Cash out", 12, True), ("Cash in", 12, True)], crow,
          "Cash taken out of / put into the drawers. Post expenses to their expense ledgers, drawings given to the owner to the "
          "Drawings (capital) account, and bank deposits as a contra entry to the bank.")
    del wb["Sheet"]
    buf = io.BytesIO()
    wb.save(buf)
    span = f"{d1}" + ("" if d1 == d2 else f"-to-{d2}")
    return _attach(buf.getvalue(), f"wise-accounts-{span}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
