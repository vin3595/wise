"""Tally Prime import files (pure functions, unit-tested).

Sales: one Sales voucher per outlet per day (the usual way for a restaurant's walk-in sales). Debits are
how the money came in (Cash, UPI, Card, Credit, Complimentary); credits are taxable sales per GST rate and
the output CGST / SGST / IGST; round off goes either side. Purchases: one Purchase voucher per supplier bill.
Tally's XML convention: a debit is ISDEEMEDPOSITIVE=Yes with a negative AMOUNT, a credit is No with a
positive AMOUNT; every voucher must add up to zero. Masters (ledgers) come in a separate file, imported once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from xml.sax.saxutils import escape

DEFAULT_LEDGERS = {
    "cash": "Cash", "upi": "UPI Collections", "card": "Card Collections", "due": "Credit Customers",
    "complimentary": "Complimentary Food", "sales": "Sales @{rate}%", "cgst": "Output CGST", "sgst": "Output SGST",
    "igst": "Output IGST", "round_off": "Round Off", "purchase": "Purchase @{rate}%", "in_cgst": "Input CGST",
    "in_sgst": "Input SGST", "cash_purchase": "Cash",
}
PARENTS = {"cash": "Cash-in-Hand", "upi": "Bank Accounts", "card": "Bank Accounts", "due": "Sundry Debtors",
           "complimentary": "Indirect Expenses", "sales": "Sales Accounts", "cgst": "Duties & Taxes", "sgst": "Duties & Taxes",
           "igst": "Duties & Taxes", "round_off": "Indirect Expenses", "purchase": "Purchase Accounts", "in_cgst": "Duties & Taxes",
           "in_sgst": "Duties & Taxes"}
DUTY_HEAD = {"cgst": "Central Tax", "sgst": "State Tax", "igst": "Integrated Tax", "in_cgst": "Central Tax", "in_sgst": "State Tax"}


def rate_text(bp: int) -> str:
    return f"{bp / 100:g}"


def amt(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    p = abs(paise)
    return f"{sign}{p // 100}.{p % 100:02d}"


@dataclass
class Voucher:
    kind: str                      # Sales | Purchase
    day: date
    number: str
    narration: str
    party: str | None = None
    lines: list[tuple[str, int]] = field(default_factory=list)   # (ledger, paise): + credit, - debit

    def add(self, ledger: str, paise: int) -> None:
        if paise:
            self.lines.append((ledger, paise))

    def balanced(self) -> bool:
        return sum(p for _, p in self.lines) == 0

    def xml(self) -> str:
        if not self.balanced():
            raise ValueError(f"Voucher {self.number} doesn't balance ({amt(sum(p for _, p in self.lines))})")
        out = [f'<VOUCHER VCHTYPE="{self.kind}" ACTION="Create" OBJVIEW="Accounting Voucher View">',
               f"<DATE>{self.day:%Y%m%d}</DATE>", f"<VOUCHERTYPENAME>{self.kind}</VOUCHERTYPENAME>",
               f"<VOUCHERNUMBER>{escape(self.number)}</VOUCHERNUMBER>", f"<NARRATION>{escape(self.narration)}</NARRATION>",
               "<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>"]
        if self.party:
            out.append(f"<PARTYLEDGERNAME>{escape(self.party)}</PARTYLEDGERNAME>")
        for ledger, p in self.lines:
            debit = p < 0
            out.append("<ALLLEDGERENTRIES.LIST>"
                       f"<LEDGERNAME>{escape(ledger)}</LEDGERNAME><ISDEEMEDPOSITIVE>{'Yes' if debit else 'No'}</ISDEEMEDPOSITIVE>"
                       f"{'<ISPARTYLEDGER>Yes</ISPARTYLEDGER>' if self.party and ledger == self.party else ''}"
                       f"<AMOUNT>{amt(p)}</AMOUNT></ALLLEDGERENTRIES.LIST>")
        out.append("</VOUCHER>")
        return "".join(out)


def envelope(company: str, bodies: list[str], report: str = "Vouchers") -> str:
    msgs = "\n".join(f'<TALLYMESSAGE xmlns:UDF="TallyUDF">{b}</TALLYMESSAGE>' for b in bodies)
    company_tag = f"<STATICVARIABLES><SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY></STATICVARIABLES>" if company else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>'
            f"<BODY><IMPORTDATA><REQUESTDESC><REPORTNAME>{report}</REPORTNAME>{company_tag}</REQUESTDESC><REQUESTDATA>\n"
            f"{msgs}\n</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>\n")


def ledger_name(ledgers: dict, key: str, rate_bp: int | None = None) -> str:
    name = (ledgers.get(key) or DEFAULT_LEDGERS[key]).strip()
    return name.replace("{rate}", rate_text(rate_bp or 0))


@dataclass
class DaySales:
    day: date
    outlet: str
    code: str
    bills: int
    first_bill: str | None
    last_bill: str | None
    by_rate: dict[int, tuple[int, int, int, int]]     # rate bp -> (taxable, cgst, sgst, igst)
    by_mode: dict[str, int]
    round_off: int


def sales_voucher(d: DaySales, ledgers: dict) -> Voucher:
    v = Voucher("Sales", d.day, f"{d.code}-{d.day:%Y%m%d}",
                f"{d.outlet}: {d.bills} bills" + (f", {d.first_bill} to {d.last_bill}" if d.first_bill else ""))
    for mode, p in sorted(d.by_mode.items()):
        v.add(ledger_name(ledgers, mode if mode in DEFAULT_LEDGERS else "cash"), -p)
    cg = sg = ig = 0
    for rate, (taxable, c, s, i) in sorted(d.by_rate.items()):
        v.add(ledger_name(ledgers, "sales", rate), taxable)
        cg, sg, ig = cg + c, sg + s, ig + i
    v.add(ledger_name(ledgers, "cgst"), cg)
    v.add(ledger_name(ledgers, "sgst"), sg)
    v.add(ledger_name(ledgers, "igst"), ig)
    v.add(ledger_name(ledgers, "round_off"), d.round_off)
    return v


@dataclass
class SupplierBill:
    day: date
    number: str
    supplier: str | None
    lines: list[tuple[int, int, int]]       # (gst bp, amount before GST, GST)


def purchase_voucher(b: SupplierBill, ledgers: dict) -> Voucher:
    party = b.supplier or ledger_name(ledgers, "cash_purchase")
    v = Voucher("Purchase", b.day, b.number, f"Supplier bill {b.number}", party=b.supplier)
    total = 0
    by_rate: dict[int, int] = {}
    tax = 0
    for bp, amount, t in b.lines:
        by_rate[bp] = by_rate.get(bp, 0) + amount
        tax += t
        total += amount + t
    for bp, amount in sorted(by_rate.items()):
        v.add(ledger_name(ledgers, "purchase", bp), -amount)
    half = tax // 2
    v.add(ledger_name(ledgers, "in_cgst"), -(tax - half))
    v.add(ledger_name(ledgers, "in_sgst"), -half)
    v.add(party, total)
    return v


def ledger_masters(names: list[tuple[str, str, str | None]], suppliers: list[tuple[str, str | None]]) -> list[str]:
    """[(ledger, parent, duty head or None)] plus supplier ledgers under Sundry Creditors (with GSTIN)."""
    out, seen = [], set()
    for name, parent, duty in names:
        if name in seen:
            continue
        seen.add(name)
        gst = (f"<TAXTYPE>GST</TAXTYPE><GSTDUTYHEAD>{duty}</GSTDUTYHEAD>" if duty else "")
        out.append(f'<LEDGER NAME="{escape(name)}" ACTION="Create"><NAME.LIST><NAME>{escape(name)}</NAME></NAME.LIST>'
                   f"<PARENT>{escape(parent)}</PARENT>{gst}</LEDGER>")
    for name, gstin in suppliers:
        if name in seen:
            continue
        seen.add(name)
        g = f"<PARTYGSTIN>{escape(gstin)}</PARTYGSTIN><GSTREGISTRATIONTYPE>Regular</GSTREGISTRATIONTYPE>" if gstin else ""
        out.append(f'<LEDGER NAME="{escape(name)}" ACTION="Create"><NAME.LIST><NAME>{escape(name)}</NAME></NAME.LIST>'
                   f"<PARENT>Sundry Creditors</PARENT>{g}</LEDGER>")
    return out
