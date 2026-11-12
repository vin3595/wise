from datetime import date

import pytest

from app.core.tally import DaySales, SupplierBill, Voucher, amt, envelope, ledger_masters, purchase_voucher, sales_voucher


def test_amounts():
    assert amt(31300) == "313.00" and amt(-5) == "-0.05" and amt(0) == "0.00"


def test_day_sales_balances_and_signs():
    d = DaySales(date(2026, 11, 10), "Paota", "PT1", 3, "PT1/2627/000001", "PT1/2627/000003",
                 {500: (59600, 1490, 1490, 0)}, {"cash": 31300, "upi": 31300}, 20)
    v = sales_voucher(d, {})
    assert v.balanced()
    x = v.xml()
    assert "<LEDGERNAME>Cash</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-313.00</AMOUNT>" in x
    assert "<LEDGERNAME>Sales @5%</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>596.00</AMOUNT>" in x
    assert "<LEDGERNAME>Round Off</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>0.20</AMOUNT>" in x
    assert "<DATE>20261110</DATE>" in x and "PT1-20261110" in x
    custom = sales_voucher(d, {"upi": "HDFC Current A/c", "sales": "Food Sales {rate}%"}).xml()
    assert "HDFC Current A/c" in custom and "Food Sales 5%" in custom


def test_unbalanced_is_refused():
    v = Voucher("Sales", date(2026, 1, 1), "X", "x")
    v.add("Cash", -100)
    v.add("Sales", 99)
    with pytest.raises(ValueError):
        v.xml()


def test_purchase_and_masters():
    b = SupplierBill(date(2026, 11, 9), "MCC-1", "Metro & Co", [(1800, 10000, 1800), (500, 20001, 1000), (0, 500, 0)])
    v = purchase_voucher(b, {})
    assert v.balanced()
    x = v.xml()
    assert "<PARTYLEDGERNAME>Metro &amp; Co</PARTYLEDGERNAME>" in x and "<AMOUNT>333.01</AMOUNT>" in x and "Purchase @18%" in x
    m = ledger_masters([("Output CGST", "Duties & Taxes", "Central Tax"), ("Output CGST", "Duties & Taxes", "Central Tax")], [("Metro & Co", "08AAACM5678L1Z9")])
    assert len(m) == 2 and "<GSTDUTYHEAD>Central Tax</GSTDUTYHEAD>" in m[0] and "<PARTYGSTIN>08AAACM5678L1Z9</PARTYGSTIN>" in m[1]
    e = envelope("Shakewallah", [x])
    assert e.startswith("<?xml") and "<SVCURRENTCOMPANY>Shakewallah</SVCURRENTCOMPANY>" in e and e.count("<TALLYMESSAGE") == 1
