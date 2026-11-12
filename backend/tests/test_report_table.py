from app.core.report_table import Col, Table, share_bp, to_csv


def table():
    t = Table("Payment-wise summary", [Col("mode", "Paid by"), Col("bills", "Bills", "int", True),
                                       Col("amount_paise", "Amount", "money", True), Col("share_bp", "Share", "percent")])
    t.rows = [{"mode": "Cash", "bills": 3, "amount_paise": 75050, "share_bp": 7505},
              {"mode": "UPI", "bills": 1, "amount_paise": 24950, "share_bp": 2495}]
    return t


def test_totals_row_sums_marked_columns_only():
    assert table().totals() == {"mode": "Total", "bills": 4, "amount_paise": 100000}
    assert Table("x", [Col("a", "A")]).totals() is None


def test_csv_opens_in_excel_with_rupees():
    out = to_csv(table(), "10 Nov 2026")
    assert out.startswith("﻿")
    lines = out[1:].splitlines()
    assert lines[0] == "Payment-wise summary: 10 Nov 2026"
    assert lines[1] == "Paid by,Bills,Amount (₹),Share (%)"
    assert lines[2] == "Cash,3,750.50,75.05"
    assert lines[-1] == "Total,4,1000.00,"


def test_csv_quotes_commas_and_blanks():
    t = Table("t", [Col("n", "Name"), Col("v", "Value", "money")])
    t.rows = [{"n": "Oreo, large", "v": None}]
    assert 'Oreo, large",' in to_csv(t)


def test_share():
    assert share_bp(1, 3) == 3333 and share_bp(5, 0) == 0
