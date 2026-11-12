import { useEffect, useState } from "react";
import { api } from "../api";
import { queuedBills } from "../offline";
import { formatPaise, inputToPaise } from "../money";
import type { Shift } from "../types";
import { Button, ErrorText, Input, useAction } from "../ui";
import CashCount, { denomTotal, type Denoms } from "./CashCount";

/** {"n500": 2, "c10": 3} -> "₹500 × 2, ₹10 coin × 3" */
const denomText = (d: Denoms) => Object.entries(d).filter(([, n]) => n).sort((a, b) => Number(b[0].slice(1)) - Number(a[0].slice(1)) || a[0].localeCompare(b[0]))
  .map(([k, n]) => `₹${k.slice(1)}${k[0] === "c" ? " coin" : ""} × ${n}`).join(", ");

/** The signed-in cashier's own shift at this outlet. Two cashiers each have their own. */
export default function ShiftBar({ outletId, onChange }: { outletId: string; onChange?: (s: Shift | null) => void }) {
  const [shift, setShift] = useState<Shift | null | undefined>(undefined);
  const [opening, setOpening] = useState("");
  const [mode, setMode] = useState<"none" | "cash" | "close">("none");
  const [amount, setAmount] = useState("");
  const [kind, setKind] = useState<"in" | "out">("out");
  const [reason, setReason] = useState("");
  const [closed, setClosed] = useState<Shift | null>(null);
  const [counts, setCounts] = useState<Denoms>({});
  const [byNote, setByNote] = useState(true);   // count note by note, or just type the total
  const [openNote, setOpenNote] = useState<string | null>(null);
  const [moveCounts, setMoveCounts] = useState<Denoms>({});
  const [moveByNote, setMoveByNote] = useState(true);
  const moveTotal = moveByNote ? denomTotal(moveCounts) : inputToPaise(amount || "0") ?? 0;
  const { busy, error, run } = useAction();
  const [drawers, setDrawers] = useState<{ id: string; name: string; is_active: boolean; in_use_by: string | null }[]>([]);
  const [drawerId, setDrawerId] = useState<string | null>(null);

  const load = async () => {
    const s = await api<Shift | null>(`/shifts/current?outlet_id=${outletId}`);
    setShift(s);
    onChange?.(s);
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load().catch(() => setShift(null)); }, [outletId]);
  useEffect(() => {
    if (shift !== null) return;
    api<typeof drawers>(`/outlets/${outletId}/drawers`).then((d) => { setDrawers(d.filter((x) => x.is_active)); setDrawerId(null); }).catch(() => setDrawers([]));
  }, [shift, outletId]);

  if (shift === undefined) return null;
  if (closed) {
    return (
      <div className="shift-bar closed">
        <span>Shift closed. Expected cash {formatPaise(closed.expected_cash_paise)}, counted {formatPaise(closed.counted_cash_paise ?? 0)},{" "}
          <strong className={closed.variance_paise ? "error" : "ok"}>difference {formatPaise(closed.variance_paise ?? 0)}</strong>.
          {closed.manager_alerted && " Your manager has been told about the difference."}
          {" "}Sales {formatPaise(closed.sales_paise)} in {closed.bills} bills.</span>
        <button className="link" onClick={() => setClosed(null)}>OK</button>
      </div>
    );
  }
  if (!shift) {
    return (
      <form className="shift-bar shift-open" onSubmit={(e) => {
        e.preventDefault();
        run(async () => {
          const body = { ...(byNote ? { outlet_id: outletId, opening_denoms: counts } : { outlet_id: outletId, opening_cash_paise: inputToPaise(opening || "0") ?? 0 }), drawer_id: drawerId };
          const opened = await api<Shift>("/shifts", { body });
          setCounts({}); setOpening("");
          setOpenNote(opened.manager_alerted ? "Your count doesn't match what the last shift left in the drawer. Your manager has been told." : null);
          await load();
        });
      }}>
        <div className="shift-open-head">
          <strong>Your shift isn't open. Count the cash in the drawer.</strong>
          <button type="button" className="link" onClick={() => setByNote(!byNote)}>{byNote ? "Just type the total" : "Count note by note"}</button>
        </div>
        {drawers.length > 1 && (
          <div className="drawer-pick" role="group" aria-label="Which drawer">
            <span className="small">Drawer:</span>
            <button type="button" className={`choice ${drawerId === null ? "on" : ""}`} onClick={() => setDrawerId(null)}>Any free one</button>
            {drawers.map((d) => <button type="button" key={d.id} disabled={!!d.in_use_by} className={`choice ${drawerId === d.id ? "on" : ""}`} onClick={() => setDrawerId(d.id)}>
              {d.name}{d.in_use_by ? ` (${d.in_use_by})` : ""}</button>)}
          </div>
        )}
        {byNote ? <CashCount value={counts} onChange={setCounts} />
          : <Input inputMode="decimal" placeholder="Opening cash ₹" value={opening} onChange={(e) => setOpening(e.target.value)} />}
        <div className="row">
          <Button disabled={busy}>Open shift with {formatPaise(byNote ? denomTotal(counts) : inputToPaise(opening || "0") ?? 0)}</Button>
        </div>
        <ErrorText error={error} />
      </form>
    );
  }
  return (
    <div className="shift-bar">
      <span>Shift open{shift.drawer_name ? ` · ${shift.drawer_name}` : ""} · {shift.bills} bills · {formatPaise(shift.sales_paise)}{shift.expected_cash_paise !== null && ` · cash in drawer ${formatPaise(shift.expected_cash_paise)}`}</span>
      {openNote && <span className="error">{openNote} <button className="link" onClick={() => setOpenNote(null)}>OK</button></span>}
      {mode === "none" && <>
        <button className="link" onClick={() => setMode("cash")}>Cash in / out</button>
        <button className="link" onClick={() => setMode("close")}>Close shift</button>
      </>}
      {mode === "cash" && (
        <form className="shift-close cash-move" onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            const body = moveByNote ? { kind, denoms: moveCounts, reason } : { kind, amount_paise: inputToPaise(amount) ?? 0, reason };
            await api(`/shifts/${shift.id}/cash`, { body });
            setAmount(""); setReason(""); setMoveCounts({}); setMode("none");
            await load();
          });
        }}>
          <div className="shift-open-head">
            <div className="tabs mini">
              <button type="button" className={kind === "out" ? "active" : ""} onClick={() => setKind("out")}>Out</button>
              <button type="button" className={kind === "in" ? "active" : ""} onClick={() => setKind("in")}>In</button>
            </div>
            <strong>{kind === "out" ? "Cash taken out of the drawer" : "Cash put into the drawer"}</strong>
            <button type="button" className="link" onClick={() => setMoveByNote(!moveByNote)}>{moveByNote ? "Just type the total" : "Count note by note"}</button>
          </div>
          {moveByNote ? <CashCount value={moveCounts} onChange={setMoveCounts} />
            : <Input inputMode="decimal" placeholder="₹" value={amount} onChange={(e) => setAmount(e.target.value)} />}
          <Input placeholder={kind === "out" ? "Reason, e.g. milk purchase" : "Reason, e.g. change from the bank"} value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="row">
            <Button disabled={busy || moveTotal <= 0 || reason.trim().length < 2}>Save {kind === "out" ? "cash out" : "cash in"} of {formatPaise(moveTotal)}</Button>
            <button type="button" className="link" onClick={() => setMode("none")}>Cancel</button>
          </div>
        </form>
      )}
      {mode === "none" && (shift.movements?.length ?? 0) > 0 && (
        <details className="moves">
          <summary>Cash in / out this shift: {shift.movements!.length}</summary>
          <ul>
            {shift.movements!.map((m, i) => (
              <li key={i}>
                <span className={m.kind === "out" ? "error" : "ok"}>{m.kind === "out" ? "−" : "+"}{formatPaise(m.amount_paise)}</span> {m.reason}
                <span className="muted small"> · {m.at ? new Date(m.at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : ""}</span>
                {m.denoms && <span className="muted small"> · {denomText(m.denoms)}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {mode === "close" && (
        <form className="shift-close" onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            const waiting = queuedBills().length;
            if (waiting) throw new Error(`${waiting} bill${waiting === 1 ? "" : "s"} made while the box was down ${waiting === 1 ? "hasn't" : "haven't"} uploaded yet. They count in this shift's cash, so close after they upload.`);
            const body = byNote ? { counted_denoms: counts } : { counted_cash_paise: inputToPaise(amount) ?? 0 };
            const s = await api<Shift>(`/shifts/${shift.id}/close`, { body });
            setClosed(s); setAmount(""); setCounts({}); setMode("none");
            await load();
          });
        }}>
          <div className="shift-open-head">
            <strong>Count the drawer to close the shift</strong>
            <button type="button" className="link" onClick={() => setByNote(!byNote)}>{byNote ? "Just type the total" : "Count note by note"}</button>
          </div>
          {byNote ? <CashCount value={counts} onChange={setCounts} />
            : <Input inputMode="decimal" placeholder="Cash counted in drawer ₹" value={amount} onChange={(e) => setAmount(e.target.value)} required />}
          <div className="row">
            <Button disabled={busy}>Close shift with {formatPaise(byNote ? denomTotal(counts) : inputToPaise(amount || "0") ?? 0)} counted</Button>
            <button type="button" className="link" onClick={() => setMode("none")}>Cancel</button>
          </div>
        </form>
      )}
      <ErrorText error={error} />
    </div>
  );
}
