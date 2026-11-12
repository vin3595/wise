#!/usr/bin/env bash
# The counter's offline GST maths (TypeScript) must equal the server's (Python) on every bill.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ESBUILD=${ESBUILD:-/opt/npm-tools/node_modules/.bin/esbuild}
python3 "$ROOT/box/tests/gst_parity.py" "$ROOT/backend" > /tmp/gst_cases.json
"$ESBUILD" "$ROOT/frontend/src/gst.ts" --format=cjs --outfile=/tmp/gst.cjs --log-level=error
node -e '
const g = require("/tmp/gst.cjs"); const cases = require("/tmp/gst_cases.json"); let bad = 0;
for (const c of cases) {
  const r = g.computeBill(c.lines, c.discount);
  const got = { total: r.total_paise, cgst: r.cgst_paise, sgst: r.sgst_paise, igst: r.igst_paise, taxable: r.taxable_paise, round: r.round_off_paise,
    per_line: r.lines.map((l) => [l.discount_paise, l.taxable_paise, l.cgst_paise, l.sgst_paise, l.igst_paise]) };
  if (JSON.stringify(got) !== JSON.stringify(c.want)) { if (bad++ < 3) console.log("MISMATCH", JSON.stringify(c), JSON.stringify(got)); }
}
console.log(bad ? `${bad} of ${cases.length} bills differ` : `all ${cases.length} bills match to the paisa`); process.exit(bad ? 1 : 0);'
