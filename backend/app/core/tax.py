"""GST calculation in integer paise.

Rates are stored in basis points (1% = 100 bp) so 2.5% CGST = 250 bp.
All amounts are integer paise; nothing here uses floats.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxGroup:
    cgst_bp: int = 250
    sgst_bp: int = 250
    igst_bp: int = 0
    inclusive: bool = False

    def __post_init__(self) -> None:
        for name in ("cgst_bp", "sgst_bp", "igst_bp"):
            v = getattr(self, name)
            if v < 0 or v > 10000:
                raise ValueError(f"{name} must be between 0 and 10000 basis points")
        if self.igst_bp and (self.cgst_bp or self.sgst_bp):
            raise ValueError("Use either IGST or CGST+SGST, not both")

    @property
    def total_bp(self) -> int:
        return self.cgst_bp + self.sgst_bp + self.igst_bp


@dataclass(frozen=True)
class LineTax:
    taxable_paise: int
    cgst_paise: int
    sgst_paise: int
    igst_paise: int

    @property
    def tax_paise(self) -> int:
        return self.cgst_paise + self.sgst_paise + self.igst_paise

    @property
    def total_paise(self) -> int:
        return self.taxable_paise + self.tax_paise


def _div_round_half_up(numerator: int, denominator: int) -> int:
    """Integer division rounded half away from zero."""
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    q, r = divmod(abs(numerator), denominator)
    if r * 2 >= denominator:
        q += 1
    return sign * q


def compute_line_tax(amount_paise: int, group: TaxGroup) -> LineTax:
    """Tax for one line amount (unit price x qty, after line discount).

    Exclusive: amount is the taxable value, tax is added on top.
    Inclusive: amount already contains tax; taxable value is backed out so that
    taxable + tax == amount exactly.
    """
    if group.total_bp == 0:
        return LineTax(amount_paise, 0, 0, 0)

    if group.inclusive:
        taxable = _div_round_half_up(amount_paise * 10000, 10000 + group.total_bp)
        tax_total = amount_paise - taxable
        if group.igst_bp:
            return LineTax(taxable, 0, 0, tax_total)
        # Split total tax between CGST and SGST proportionally; any odd paisa goes to CGST.
        if group.cgst_bp == group.sgst_bp:
            sgst = tax_total // 2
        else:
            sgst = _div_round_half_up(tax_total * group.sgst_bp, group.total_bp)
        cgst = tax_total - sgst
        return LineTax(taxable, cgst, sgst, 0)

    taxable = amount_paise
    if group.igst_bp:
        return LineTax(taxable, 0, 0, _div_round_half_up(taxable * group.igst_bp, 10000))
    cgst = _div_round_half_up(taxable * group.cgst_bp, 10000)
    sgst = _div_round_half_up(taxable * group.sgst_bp, 10000)
    return LineTax(taxable, cgst, sgst, 0)


def round_off(total_paise: int) -> tuple[int, int]:
    """Round a bill total to the nearest rupee. Returns (rounded_total, round_off_delta)."""
    rounded = _div_round_half_up(total_paise, 100) * 100
    return rounded, rounded - total_paise


def rupees_to_paise(value: str | int | float) -> int:
    """Parse a rupee amount like '149', '149.5' or 149.50 into paise without float drift."""
    s = str(value).strip().replace(",", "").replace("₹", "")
    if not s:
        raise ValueError("empty amount")
    negative = s.startswith("-")
    if negative:
        s = s[1:]
    if s.count(".") > 1:
        raise ValueError(f"invalid amount: {value!r}")
    whole, _, frac = s.partition(".")
    if not whole:
        whole = "0"
    if not whole.isdigit() or (frac and not frac.isdigit()):
        raise ValueError(f"invalid amount: {value!r}")
    if len(frac) > 2:
        # Round half up on the third decimal.
        extra = int(frac[2])
        frac = frac[:2]
        paise = int(whole) * 100 + int(frac)
        if extra >= 5:
            paise += 1
    else:
        paise = int(whole) * 100 + int(frac.ljust(2, "0") or "0")
    return -paise if negative else paise


def percent_to_bp(value: str | int | float) -> int:
    """'2.5' -> 250 basis points."""
    paise_like = rupees_to_paise(value)  # same 2-decimal parsing
    return paise_like
