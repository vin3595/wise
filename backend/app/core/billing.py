"""Bill maths: discounts, GST per line, round-off, invoice numbers, payment checks.

Everything is integer paise. A bill-level discount is spread across lines in proportion to
their value (largest-remainder, so the parts add up exactly) and applied BEFORE tax, which is
how GST treats a discount shown on the invoice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .tax import LineTax, TaxGroup, compute_line_tax, round_off

PAYMENT_MODES = ("cash", "upi", "card", "due", "complimentary")
MAX_INVOICE_LEN = 16


@dataclass(frozen=True)
class LineIn:
    key: str
    qty: int
    unit_paise: int          # variant price + add-ons, as sold at this outlet
    tax: TaxGroup


@dataclass(frozen=True)
class Discount:
    kind: str                # "percent" | "flat"
    value: int               # percent in basis points (10% = 1000) or flat paise

    def __post_init__(self) -> None:
        if self.kind not in ("percent", "flat"):
            raise ValueError("Discount must be percent or flat")
        if self.value < 0:
            raise ValueError("Discount can't be negative")
        if self.kind == "percent" and self.value > 10000:
            raise ValueError("Discount can't be more than 100%")


@dataclass(frozen=True)
class LineOut:
    key: str
    gross_paise: int
    discount_paise: int
    tax: LineTax
    rate_bp: int
    inclusive: bool

    @property
    def net_paise(self) -> int:
        return self.gross_paise - self.discount_paise


@dataclass
class BillCalc:
    lines: list[LineOut] = field(default_factory=list)
    gross_paise: int = 0
    discount_paise: int = 0
    taxable_paise: int = 0
    cgst_paise: int = 0
    sgst_paise: int = 0
    igst_paise: int = 0
    round_off_paise: int = 0
    total_paise: int = 0

    @property
    def tax_paise(self) -> int:
        return self.cgst_paise + self.sgst_paise + self.igst_paise

    @property
    def discount_bp(self) -> int:
        """Discount as a share of the bill, for the manager-approval limit."""
        return 0 if not self.gross_paise else (self.discount_paise * 10000 + self.gross_paise - 1) // self.gross_paise

    def tax_summary(self) -> list[dict]:
        """Taxable value and tax per GST rate, as printed at the bottom of the bill and for GSTR-1."""
        by_rate: dict[int, dict] = {}
        for ln in self.lines:
            row = by_rate.setdefault(ln.rate_bp, {"rate_bp": ln.rate_bp, "taxable_paise": 0, "cgst_paise": 0,
                                                  "sgst_paise": 0, "igst_paise": 0})
            row["taxable_paise"] += ln.tax.taxable_paise
            row["cgst_paise"] += ln.tax.cgst_paise
            row["sgst_paise"] += ln.tax.sgst_paise
            row["igst_paise"] += ln.tax.igst_paise
        return [by_rate[k] for k in sorted(by_rate)]


def allocate(total: int, weights: list[int]) -> list[int]:
    """Split `total` in proportion to weights; parts are integers that sum exactly to total."""
    wsum = sum(weights)
    if total == 0 or wsum == 0:
        return [0] * len(weights)
    raw = [total * w for w in weights]
    parts = [r // wsum for r in raw]
    remainders = sorted(range(len(weights)), key=lambda i: (-(raw[i] % wsum), i))
    for i in remainders[: total - sum(parts)]:
        parts[i] += 1
    return parts


def compute_bill(lines: list[LineIn], discount: Discount | None = None, apply_round_off: bool = True) -> BillCalc:
    if any(ln.qty <= 0 for ln in lines):
        raise ValueError("Quantity must be at least 1")
    gross = [ln.qty * ln.unit_paise for ln in lines]
    gross_total = sum(gross)
    disc_total = 0
    if discount is not None and discount.value:
        if discount.kind == "percent":
            disc_total = (gross_total * discount.value + 5000) // 10000
        else:
            disc_total = discount.value
        if disc_total > gross_total:
            raise ValueError("Discount can't be more than the bill amount")
    parts = allocate(disc_total, gross)

    calc = BillCalc(gross_paise=gross_total, discount_paise=disc_total)
    for ln, g, d in zip(lines, gross, parts):
        tax = compute_line_tax(g - d, ln.tax)
        calc.lines.append(LineOut(ln.key, g, d, tax, ln.tax.total_bp, ln.tax.inclusive))
        calc.taxable_paise += tax.taxable_paise
        calc.cgst_paise += tax.cgst_paise
        calc.sgst_paise += tax.sgst_paise
        calc.igst_paise += tax.igst_paise
    pre_round = calc.taxable_paise + calc.tax_paise
    if apply_round_off:
        calc.total_paise, calc.round_off_paise = round_off(pre_round)
    else:
        calc.total_paise = pre_round
    return calc


# ------------------------------------------------------------------ invoice numbers

def fiscal_year_start(d: date, start_month: int = 4) -> int:
    """Indian FY Apr-Mar: 10 Nov 2026 -> 2026; 10 Feb 2027 -> 2026."""
    return d.year if d.month >= start_month else d.year - 1


def fy_label(start_year: int) -> str:
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def invoice_number(outlet_code: str, fy_start: int, seq: int) -> str:
    """'PT1/2627/000123'. GST Rule 46(b): consecutive, unique in the FY, max 16 characters,
    only letters, digits, '-' and '/'. Outlet codes are 2-4 characters so 6 digits always fit."""
    if not (2 <= len(outlet_code) <= 4 and outlet_code.isalnum()):
        raise ValueError("Outlet code must be 2-4 letters or digits")
    if seq < 1:
        raise ValueError("Invoice sequence starts at 1")
    prefix = f"{outlet_code.upper()}/{fy_label(fy_start)}/"
    digits = max(6, len(str(seq)))
    number = f"{prefix}{seq:0{digits}d}"
    if len(number) > MAX_INVOICE_LEN:
        raise ValueError("Invoice number series is full for this financial year")
    return number


# ------------------------------------------------------------------ payments

@dataclass(frozen=True)
class PaymentIn:
    mode: str
    amount_paise: int


def validate_payments(total_paise: int, payments: list[PaymentIn]) -> None:
    """Payments must add up exactly to the bill. Complimentary is all-or-nothing."""
    if not payments:
        raise ValueError("Add a payment")
    for p in payments:
        if p.mode not in PAYMENT_MODES:
            raise ValueError(f"Unknown payment mode {p.mode!r}")
        if p.amount_paise <= 0 and total_paise > 0:
            raise ValueError("Each payment must be more than zero")
    modes = [p.mode for p in payments]
    if "complimentary" in modes and len(payments) > 1:
        raise ValueError("A complimentary bill can't be combined with other payments")
    if len(set(modes)) != len(modes):
        raise ValueError("Combine payments of the same mode into one")
    paid = sum(p.amount_paise for p in payments)
    if paid != total_paise:
        raise ValueError(f"Payments add up to {paid / 100:.2f} but the bill is {total_paise / 100:.2f}")
