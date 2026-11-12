// Money is integer paise everywhere; these helpers only convert for display and input.

const fmt = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });

export function formatPaise(paise: number): string {
  return fmt.format(paise / 100).replace(/\.00$/, "");
}

export function paiseToInput(paise: number): string {
  const r = Math.floor(paise / 100);
  const p = paise % 100;
  return p ? `${r}.${String(p).padStart(2, "0")}` : String(r);
}

/** "149.5" -> 14950. Returns null when the text isn't a valid amount. */
export function inputToPaise(text: string): number | null {
  const s = text.trim().replace(/[,₹\s]/g, "");
  if (!/^\d*(\.\d{0,2})?$/.test(s) || s === "" || s === ".") return null;
  const [whole, frac = ""] = s.split(".");
  return Number(whole || "0") * 100 + Number(frac.padEnd(2, "0"));
}

export function bpToPercent(bp: number): string {
  return (bp / 100).toString();
}
