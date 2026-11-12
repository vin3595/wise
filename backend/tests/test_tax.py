import pytest

from app.core.tax import TaxGroup, compute_line_tax, percent_to_bp, round_off, rupees_to_paise


def test_exclusive_5_percent():
    t = compute_line_tax(14900, TaxGroup(250, 250))  # Rs 149
    assert (t.taxable_paise, t.cgst_paise, t.sgst_paise) == (14900, 373, 373)  # 372.5 rounds up
    assert t.total_paise == 15646


def test_inclusive_5_percent_adds_back_exactly():
    for amount in (100, 14900, 9999, 39900, 1, 7):
        t = compute_line_tax(amount, TaxGroup(250, 250, inclusive=True))
        assert t.total_paise == amount
        assert abs(t.cgst_paise - t.sgst_paise) <= 1


def test_inclusive_149_breakup():
    t = compute_line_tax(14900, TaxGroup(250, 250, inclusive=True))
    assert t.taxable_paise == 14190  # 149 / 1.05 = 141.904...
    assert t.tax_paise == 710
    assert t.cgst_paise + t.sgst_paise == 710


def test_igst_and_zero_rate():
    assert compute_line_tax(10000, TaxGroup(0, 0, 1800)).igst_paise == 1800
    z = compute_line_tax(10000, TaxGroup(0, 0, 0))
    assert z.tax_paise == 0 and z.total_paise == 10000


def test_invalid_groups():
    with pytest.raises(ValueError):
        TaxGroup(250, 250, 500)
    with pytest.raises(ValueError):
        TaxGroup(-1, 0)


def test_round_off():
    assert round_off(15646) == (15600, -46)
    assert round_off(15650) == (15700, 50)
    assert round_off(15600) == (15600, 0)


@pytest.mark.parametrize("raw,paise", [
    ("149", 14900), ("149.5", 14950), ("149.50", 14950), (149, 14900), (149.0, 14900),
    ("1,499", 149900), ("₹99", 9900), ("0.005", 1), ("0.004", 0), (".5", 50),
])
def test_rupees_to_paise(raw, paise):
    assert rupees_to_paise(raw) == paise


@pytest.mark.parametrize("raw", ["", "abc", "1.2.3", "12a"])
def test_rupees_to_paise_rejects(raw):
    with pytest.raises(ValueError):
        rupees_to_paise(raw)


def test_percent_to_bp():
    assert percent_to_bp("2.5") == 250
    assert percent_to_bp(9) == 900
