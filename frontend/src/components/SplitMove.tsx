import { useState } from "react";
import { api } from "../api";
import type { Order } from "../types";
import { Button, ErrorText, Field, Input, useAction } from "../ui";

/** Split a bill by items (they become their own order, T4 -> T4/2) or move items / the whole table. */
export default function SplitMove({ order, mode, tables, onClose, onDone }: {
  order: Order; mode: "split" | "move"; tables: string[]; onClose: () => void; onDone: (o: Order) => void;
}) {
  const lines = order.kots.filter((k) => k.status !== "cancelled").flatMap((k) => k.lines);
  const [pick, setPick] = useState<Record<string, number>>({});
  const [to, setTo] = useState("");
  const [whole, setWhole] = useState(mode === "move");
  const { busy, error, run } = useAction();
  const picked = Object.entries(pick).filter(([, q]) => q > 0);
  const all = lines.every((l) => (pick[l.id] || 0) === l.qty);
  const set = (id: string, q: number, max: number) => setPick((p) => ({ ...p, [id]: Math.max(0, Math.min(max, q)) }));
  const free = tables.filter((t) => t !== order.table_label);
  const go = () => run(async () => {
    const items = picked.map(([order_item_id, qty]) => ({ order_item_id, qty }));
    const o = mode === "split"
      ? await api<Order>(`/orders/${order.id}/split`, { body: { items } })
      : await api<Order>(`/orders/${order.id}/move`, { body: { to_table: to.trim(), items: whole ? null : items } });
    onDone(o);
  });
  const ok = mode === "split" ? picked.length > 0 && !all : to.trim().length > 0 && (whole || picked.length > 0);
  return (
    <div className="modal-back" role="dialog" aria-modal="true" aria-label={mode === "split" ? "Split bill" : "Move table"}>
      <div className="modal split-move">
        <h2>{mode === "split" ? `Split bill: ${order.table_label ? `Table ${order.table_label}` : `#${order.token_no}`}` : `Move from Table ${order.table_label}`}</h2>
        {mode === "move" && <>
          <Field label="To table">
            <Input value={to} list="move-tables" placeholder="e.g. T7" maxLength={20} onChange={(e) => setTo(e.target.value)} autoFocus />
            <datalist id="move-tables">{free.map((t) => <option key={t} value={t} />)}</datalist>
          </Field>
          {free.length > 0 && <div className="row wrap">{free.slice(0, 16).map((t) => <button key={t} className={`choice ${to === t ? "on" : ""}`} onClick={() => setTo(t)}>{t}</button>)}</div>}
          <div className="seg two">
            <button className={whole ? "active" : ""} onClick={() => setWhole(true)}>Whole table</button>
            <button className={!whole ? "active" : ""} onClick={() => setWhole(false)}>Some items</button>
          </div>
          <p className="muted small">If that table already has an open order, these join it.</p>
        </>}
        {(mode === "split" || !whole) && <>
          <p className="muted small">{mode === "split" ? "Pick what goes on the other bill. It becomes its own order at the same table, billed separately." : "Pick the items to move."}</p>
          <ul className="pick-lines">
            {lines.map((l) => (
              <li key={l.id}>
                <span className="grow">{l.item_name}{l.variant_name !== "Regular" ? ` (${l.variant_name})` : ""} <span className="muted small">of {l.qty}</span></span>
                <span className="qty-ctl">
                  <button aria-label={`One less ${l.item_name}`} onClick={() => set(l.id, (pick[l.id] || 0) - 1, l.qty)}>−</button>
                  <span>{pick[l.id] || 0}</span>
                  <button aria-label={`One more ${l.item_name}`} onClick={() => set(l.id, (pick[l.id] || 0) + 1, l.qty)}>+</button>
                </span>
                <button className="link small" onClick={() => set(l.id, (pick[l.id] || 0) === l.qty ? 0 : l.qty, l.qty)}>{(pick[l.id] || 0) === l.qty ? "None" : "All"}</button>
              </li>
            ))}
          </ul>
          {mode === "split" && all && <p className="error small">That's everything: leave at least one item on this bill.</p>}
        </>}
        <ErrorText error={error} />
        <div className="row">
          <Button disabled={!ok || busy} onClick={go}>{mode === "split" ? "Split off these items" : whole ? `Move table to ${to || "…"}` : `Move to ${to || "…"}`}</Button>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </div>
  );
}
