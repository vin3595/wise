import { useState } from "react";
import { formatPaise } from "../money";

export type Denoms = Record<string, number>;
const COINS = [20, 10, 5, 2, 1];
const NOTES = [500, 200, 100, 50, 20, 10];
export const denomTotal = (d: Denoms) => Object.entries(d).reduce((s, [k, n]) => s + Number(k.slice(1)) * (n || 0) * 100, 0);

/** Count the drawer note by note and coin by coin; the total adds itself up.
 *  Big notes first (that's how a drawer is counted); coins fold away until needed. */
export default function CashCount({ value, onChange }: { value: Denoms; onChange: (d: Denoms) => void }) {
  const set = (k: string, n: number) => onChange({ ...value, [k]: Math.max(0, Math.min(n, 100000)) });
  const coinTotal = COINS.reduce((s, v) => s + v * (value[`c${v}`] || 0) * 100, 0);
  const [coinsOpen, setCoinsOpen] = useState(coinTotal > 0);
  const count = (p: string) => Object.entries(value).filter(([k]) => k[0] === p).reduce((s, [, n]) => s + (n || 0), 0);
  const notes = count("n"), coins = count("c");
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const pieces = [notes && plural(notes, "note"), coins && plural(coins, "coin")].filter(Boolean).join(", ");

  const row = (v: number, coin: boolean) => {
    const k = `${coin ? "c" : "n"}${v}`, n = value[k] || 0, what = coin ? "coin" : "note";
    return (
      <li key={k} className={`cc-row ${n ? "has" : ""}`}>
        <span className={`cc-face ${coin ? "coin" : `note n${v}`}`}>₹{v}</span>
        <span className="cc-step">
          <button type="button" className="cc-btn" disabled={!n} onClick={() => set(k, n - 1)} aria-label={`One less ₹${v} ${what}`}>−</button>
          <input className="cc-n" inputMode="numeric" aria-label={`${coin ? "Coins" : "Notes"} of ₹${v}`}
            value={n || ""} placeholder="0" onFocus={(e) => e.target.select()}
            onChange={(e) => { const x = parseInt(e.target.value.replace(/\D/g, ""), 10); set(k, Number.isFinite(x) ? x : 0); }} />
          <button type="button" className="cc-btn plus" onClick={() => set(k, n + 1)} aria-label={`One more ₹${v} ${what}`}>+</button>
        </span>
        <span className="cc-sum">{n ? formatPaise(v * n * 100) : ""}</span>
      </li>
    );
  };
  return (
    <div className="cc">
      <ul className="cc-list" aria-label="Notes">{NOTES.map((v) => row(v, false))}</ul>
      <button type="button" className="cc-coins" aria-expanded={coinsOpen} onClick={() => setCoinsOpen(!coinsOpen)}>
        <span>Coins</span><span className="muted">{coinTotal ? formatPaise(coinTotal) : coinsOpen ? "" : "Tap to count"}</span><span aria-hidden>{coinsOpen ? "▴" : "▾"}</span>
      </button>
      {coinsOpen && <ul className="cc-list" aria-label="Coins">{COINS.map((v) => row(v, true))}</ul>}
      <div className="cc-total" aria-live="polite">
        <span>Total{pieces ? ` · ${pieces}` : ""}</span>
        <strong>{formatPaise(denomTotal(value))}</strong>
      </div>
    </div>
  );
}
