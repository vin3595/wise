// GST maths for offline billing: an exact copy of backend/app/core/tax.py + billing.py (integer paise,
// half-up rounding), so a bill made at the counter while the box is down matches the server to the paisa.
// Checked against the Python version by box/tests/gst_parity.sh.

export interface TaxRate { cgst_bp: number; sgst_bp: number; igst_bp: number; inclusive: boolean }
export interface CalcLine { gross_paise: number; discount_paise: number; taxable_paise: number; cgst_paise: number; sgst_paise: number; igst_paise: number; rate_bp: number; inclusive: boolean }
export interface Calc { lines: CalcLine[]; gross_paise: number; discount_paise: number; taxable_paise: number; cgst_paise: number; sgst_paise: number; igst_paise: number; round_off_paise: number; total_paise: number }

function divHalfUp(n: number, d: number): number {
  const sign = n < 0 ? -1 : 1;
  const a = Math.abs(n);
  let q = Math.floor(a / d);
  const r = a - q * d;
  if (r * 2 >= d) q += 1;
  return sign * q;
}

export function lineTax(amount: number, g: TaxRate): { taxable: number; cgst: number; sgst: number; igst: number } {
  const total = g.cgst_bp + g.sgst_bp + g.igst_bp;
  if (total === 0) return { taxable: amount, cgst: 0, sgst: 0, igst: 0 };
  if (g.inclusive) {
    const taxable = divHalfUp(amount * 10000, 10000 + total);
    const tax = amount - taxable;
    if (g.igst_bp) return { taxable, cgst: 0, sgst: 0, igst: tax };
    const sgst = g.cgst_bp === g.sgst_bp ? Math.floor(tax / 2) : divHalfUp(tax * g.sgst_bp, total);
    return { taxable, cgst: tax - sgst, sgst, igst: 0 };
  }
  if (g.igst_bp) return { taxable: amount, cgst: 0, sgst: 0, igst: divHalfUp(amount * g.igst_bp, 10000) };
  return { taxable: amount, cgst: divHalfUp(amount * g.cgst_bp, 10000), sgst: divHalfUp(amount * g.sgst_bp, 10000), igst: 0 };
}

export function allocate(total: number, weights: number[]): number[] {
  const wsum = weights.reduce((a, b) => a + b, 0);
  if (total === 0 || wsum === 0) return weights.map(() => 0);
  const raw = weights.map((w) => total * w);
  const parts = raw.map((r) => Math.floor(r / wsum));
  const order = weights.map((_, i) => i).sort((a, b) => (raw[b] % wsum) - (raw[a] % wsum) || a - b);
  let left = total - parts.reduce((a, b) => a + b, 0);
  for (const i of order) { if (left <= 0) break; parts[i] += 1; left -= 1; }
  return parts;
}

export function computeBill(lines: { qty: number; unit_paise: number; tax: TaxRate }[], discount?: { kind: "percent" | "flat"; value: number } | null): Calc {
  const gross = lines.map((l) => l.qty * l.unit_paise);
  const grossTotal = gross.reduce((a, b) => a + b, 0);
  let disc = 0;
  if (discount && discount.value) disc = discount.kind === "percent" ? Math.floor((grossTotal * discount.value + 5000) / 10000) : discount.value;
  if (disc > grossTotal) throw new Error("Discount can't be more than the bill amount");
  const parts = allocate(disc, gross);
  const c: Calc = { lines: [], gross_paise: grossTotal, discount_paise: disc, taxable_paise: 0, cgst_paise: 0, sgst_paise: 0, igst_paise: 0, round_off_paise: 0, total_paise: 0 };
  lines.forEach((l, i) => {
    const t = lineTax(gross[i] - parts[i], l.tax);
    c.lines.push({ gross_paise: gross[i], discount_paise: parts[i], taxable_paise: t.taxable, cgst_paise: t.cgst, sgst_paise: t.sgst, igst_paise: t.igst, rate_bp: l.tax.cgst_bp + l.tax.sgst_bp + l.tax.igst_bp, inclusive: l.tax.inclusive });
    c.taxable_paise += t.taxable; c.cgst_paise += t.cgst; c.sgst_paise += t.sgst; c.igst_paise += t.igst;
  });
  const pre = c.taxable_paise + c.cgst_paise + c.sgst_paise + c.igst_paise;
  c.total_paise = divHalfUp(pre, 100) * 100;
  c.round_off_paise = c.total_paise - pre;
  return c;
}

export function fyStart(d: Date, startMonth = 4): number {
  return d.getMonth() + 1 >= startMonth ? d.getFullYear() : d.getFullYear() - 1;
}
export const fyLabel = (y: number) => `${String(y % 100).padStart(2, "0")}${String((y + 1) % 100).padStart(2, "0")}`;
export const invoiceNumber = (code: string, fy: number, seq: number) => `${code.toUpperCase()}/${fyLabel(fy)}/${String(seq).padStart(6, "0")}`;
