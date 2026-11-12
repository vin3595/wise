import { formatPaise } from "../money";

export type Denoms = Record<string, number>;
const COINS = [1, 2, 5, 10, 20];
const NOTES = [10, 20, 50, 100, 200, 500];
export const denomTotal = (d: Denoms) => Object.entries(d).reduce((s, [k, n]) => s + Number(k.slice(1)) * (n || 0) * 100, 0);

/** Count the drawer coin by coin and note by note; the total adds itself up. */
export default function CashCount({ value, onChange }: { value: Denoms; onChange: (d: Denoms) => void }) {
  const set = (k: string, n: number) => onChange({ ...value, [k]: Math.max(0, Math.min(n, 100000)) });
  const row = (label: string, prefix: "c" | "n", list: number[]) => (
    <div className="cc-group">
      <span className="cc-label">{label}</span>
      <div className="cc-grid">
        {list.map((v) => {
          const k = `${prefix}${v}`, n = value[k] || 0;
          return (
            <div key={k} className={`cc-cell ${n ? "has" : ""}`}>
              <span className="cc-face">₹{v}</span>
              <span className="cc-x">×</span>
              <span className="cc-step">
                <button type="button" className="cc-btn" disabled={!n} onClick={() => set(k, n - 1)} aria-label={`One less ₹${v} ${label === "Coins" ? "coin" : "note"}`}>−</button>
                <input className="input cc-n" inputMode="numeric" aria-label={`${label === "Coins" ? "Coins" : "Notes"} of ₹${v}`}
                  value={n || ""} placeholder="0"
                  onChange={(e) => { const x = parseInt(e.target.value.replace(/\D/g, ""), 10); set(k, Number.isFinite(x) ? x : 0); }} />
                <button type="button" className="cc-btn" onClick={() => set(k, n + 1)} aria-label={`One more ₹${v} ${label === "Coins" ? "coin" : "note"}`}>+</button>
              </span>
              <span className="cc-sum">{n ? formatPaise(v * n * 100) : ""}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
  return (
    <div className="cc">
      {row("Notes", "n", NOTES)}
      {row("Coins", "c", COINS)}
      <div className="cc-total"><span>Total</span><strong>{formatPaise(denomTotal(value))}</strong></div>
    </div>
  );
}
