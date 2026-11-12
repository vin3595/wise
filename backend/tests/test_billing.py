from datetime import date

import pytest

from app.core.billing import (
    Discount, LineIn, PaymentIn, allocate, compute_bill, fiscal_year_start, fy_label, invoice_number,
    validate_payments,
)
from app.core.tax import TaxGroup

GST5 = TaxGroup(250, 250)
GST5_INCL = TaxGroup(250, 250, inclusive=True)
GST18 = TaxGroup(900, 900)


def test_simple_exclusive_bill():
    b = compute_bill([LineIn("oreo", 2, 14900, GST5), LineIn("fries", 1, 12900, GST5)])
    assert b.gross_paise == 42700 and b.discount_paise == 0
    assert b.taxable_paise == 42700
    assert b.cgst_paise == b.sgst_paise == 1068  # 745 on 298.00 + 323 on 129.00 (322.5 rounds up)
    pre = b.taxable_paise + b.tax_paise
    assert b.total_paise % 100 == 0 and b.total_paise - pre == b.round_off_paise
    assert abs(b.round_off_paise) <= 50


def test_inclusive_bill_total_equals_menu_price():
    b = compute_bill([LineIn("oreo", 1, 14900, GST5_INCL), LineIn("cc", 3, 9900, GST5_INCL)], apply_round_off=False)
    assert b.total_paise == 14900 + 3 * 9900
    assert b.taxable_paise + b.tax_paise == b.total_paise


def test_percent_discount_before_tax():
    b = compute_bill([LineIn("a", 1, 10000, GST5)], Discount("percent", 1000))
    assert b.discount_paise == 1000
    assert b.taxable_paise == 9000
    assert b.cgst_paise == 225 and b.sgst_paise == 225
    assert b.total_paise == 9500  # 9450 rounds to 95.00
    assert b.round_off_paise == 50
    assert b.discount_bp == 1000


def test_flat_discount_spread_exactly_across_lines():
    lines = [LineIn("a", 1, 10000, GST5), LineIn("b", 1, 20000, GST18), LineIn("c", 1, 333, GST5)]
    b = compute_bill(lines, Discount("flat", 1001))
    assert sum(ln.discount_paise for ln in b.lines) == 1001
    assert [ln.discount_paise for ln in b.lines] == [330, 660, 11]
    assert all(ln.discount_paise <= ln.gross_paise for ln in b.lines)


def test_discount_too_big():
    with pytest.raises(ValueError):
        compute_bill([LineIn("a", 1, 1000, GST5)], Discount("flat", 2000))
    with pytest.raises(ValueError):
        Discount("percent", 10001)


def test_full_discount_gives_zero_bill():
    b = compute_bill([LineIn("a", 2, 14900, GST5)], Discount("percent", 10000))
    assert b.total_paise == 0 and b.tax_paise == 0


def test_tax_summary_groups_by_rate():
    b = compute_bill([LineIn("a", 1, 10000, GST5), LineIn("b", 1, 10000, GST18), LineIn("c", 1, 5000, GST5)])
    s = b.tax_summary()
    assert [r["rate_bp"] for r in s] == [500, 1800]
    assert s[0]["taxable_paise"] == 15000 and s[0]["cgst_paise"] == 375
    assert s[1]["cgst_paise"] == 900


def test_allocate():
    assert allocate(10, [1, 1, 1]) == [4, 3, 3]
    assert allocate(0, [5, 5]) == [0, 0]
    assert allocate(7, [0, 0]) == [0, 0]
    assert sum(allocate(9999, [3, 7, 11, 13])) == 9999


def test_fiscal_year():
    assert fiscal_year_start(date(2026, 11, 10)) == 2026
    assert fiscal_year_start(date(2027, 3, 31)) == 2026
    assert fiscal_year_start(date(2027, 4, 1)) == 2027
    assert fy_label(2026) == "2627" and fy_label(2099) == "9900"


def test_invoice_number_rules():
    assert invoice_number("PT1", 2026, 123) == "PT1/2627/000123"
    n = invoice_number("ABCD", 2026, 999999)
    assert n == "ABCD/2627/999999" and len(n) == 16
    assert invoice_number("PT", 2026, 1234567) == "PT/2627/1234567"  # shorter code leaves room for 7 digits
    with pytest.raises(ValueError):
        invoice_number("ABCD", 2026, 1_000_000)  # would be 17 characters
    with pytest.raises(ValueError):
        invoice_number("ABCDE", 2026, 1)
    with pytest.raises(ValueError):
        invoice_number("PT1", 2026, 0)


def test_payments():
    validate_payments(42700, [PaymentIn("cash", 20000), PaymentIn("upi", 22700)])
    validate_payments(0, [PaymentIn("cash", 0)])
    validate_payments(5000, [PaymentIn("complimentary", 5000)])
    for bad in ([], [PaymentIn("cash", 100)], [PaymentIn("cheque", 42700)],
                [PaymentIn("complimentary", 20000), PaymentIn("cash", 22700)],
                [PaymentIn("cash", 20000), PaymentIn("cash", 22700)]):
        with pytest.raises(ValueError):
            validate_payments(42700, bad)
