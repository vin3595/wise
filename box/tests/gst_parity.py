"""Random bills computed by the server's Python GST code, for comparison with frontend/src/gst.ts."""
import json, random, sys
sys.path.insert(0, sys.argv[1])
from app.core.billing import Discount, LineIn, compute_bill
from app.core.tax import TaxGroup
random.seed(7)
cases = []
rates = [(250, 250, 0), (0, 0, 500), (600, 600, 0), (900, 900, 0), (0, 0, 0), (250, 300, 0)]
for _ in range(3000):
    lines = []
    for _ in range(random.randint(1, 6)):
        c, s, i = random.choice(rates)
        lines.append({"qty": random.randint(1, 5), "unit_paise": random.randint(1, 60000),
                      "tax": {"cgst_bp": c, "sgst_bp": s, "igst_bp": i, "inclusive": random.random() < 0.4}})
    disc = None
    if random.random() < 0.4:
        gross = sum(l["qty"] * l["unit_paise"] for l in lines)
        disc = random.choice([{"kind": "percent", "value": random.randint(0, 5000)}, {"kind": "flat", "value": random.randint(0, gross)}])
    calc = compute_bill([LineIn(str(n), l["qty"], l["unit_paise"], TaxGroup(**l["tax"])) for n, l in enumerate(lines)],
                        Discount(disc["kind"], disc["value"]) if disc else None)
    cases.append({"lines": lines, "discount": disc, "want": {"total": calc.total_paise, "cgst": calc.cgst_paise, "sgst": calc.sgst_paise,
                  "igst": calc.igst_paise, "taxable": calc.taxable_paise, "round": calc.round_off_paise,
                  "per_line": [[l.discount_paise, l.tax.taxable_paise, l.tax.cgst_paise, l.tax.sgst_paise, l.tax.igst_paise] for l in calc.lines]}})
json.dump(cases, sys.stdout)
