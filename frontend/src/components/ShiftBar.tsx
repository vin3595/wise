import { useEffect, useState } from "react";
import { api } from "../api";
import { queuedBills } from "../offline";
import { formatPaise, inputToPaise } from "../money";
import type { CashPurpose, Shift } from "../types";
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
  // What the cash is for. Out: expense (with category), drawing (to the owner or the bank), supplier, other.
  const [purpose, setPurpose] = useState<CashPurpose>("expense");
  const [category, setCategory] = useState("");
  const [handed, setHanded] = useState<"owner" | "bank">("owner");
  const [paidTo, setPaidTo] = useState("");
  const [tips, setTips] = useState<{ categories: string[]; owners: string[]; banks: string[]; suppliers: string[] }>({ categories: [], owners: [], banks: [], suppliers: [] });
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

  useEffect(() => {
    if (mode !== "cash") return;
    api<typeof tips>("/cash/suggestions").then(setTips).catch(() => undefined);
  }, [mode]);
  const pickKind = (k: "in" | "out") => { setKind(k); setPurpose(k === "out" ? "expense" : "topup"); setPaidTo(""); setCategory(""); };
  const pickPurpose = (p: CashPurpose) => { setPurpose(p); setPaidTo(""); setHanded("owner"); };
  const ready = moveTotal > 0 && (
    purpose === "expense" ? category.trim().length > 0
      : purpose === "drawing" || purpose === "supplier" ? paidTo.trim().length > 0
        : purpose === "other" ? reason.trim().length >= 2 : true);
  const saveLabel = { expense: "expense", drawing: handed === "bank" ? "bank deposit" : "drawing", supplier: "supplier payment", topup: "top-up", change: "change", other: kind === "out" ? "cash out" : "cash in" }[purpose];
  const names = purpose === "drawing" ? (handed === "owner" ? tips.owners : tips.banks) : purpose === "supplier" ? tips.suppliers : [];

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
        <button className="link" onClick={() => { pickKind("out"); setHanded("owner"); setMode("cash"); }}>Cash in / out</button>
        <button className="link" onClick={() => setMode("close")}>Close shift</button>
      </>}
      {mode === "cash" && (
        <form className="shift-close cash-move" onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            const what = { purpose, category: purpose === "expense" ? category : null, handed: purpose === "drawing" ? handed : null,
              paid_to: purpose === "drawing" || purpose === "supplier" ? paidTo : null, reason };
            const body = moveByNote ? { kind, denoms: moveCounts, ...what } : { kind, amount_paise: inputToPaise(amount) ?? 0, ...what };
            await api(`/shifts/${shift.id}/cash`, { body });
            setAmount(""); setReason(""); setMoveCounts({}); setCategory(""); setPaidTo(""); setMode("none");
            await load();
          });
        }}>
          <div className="shift-open-head">
            <div className="tabs mini">
              <button type="button" className={kind === "out" ? "active" : ""} onClick={() => pickKind("out")}>Out</button>
              <button type="button" className={kind === "in" ? "active" : ""} onClick={() => pickKind("in")}>In</button>
            </div>
            <strong>{kind === "out" ? "Cash taken out of the drawer" : "Cash put into the drawer"}</strong>
          </div>
          <div className="chips cm-purpose" role="radiogroup" aria-label="What is it for">
            {(kind === "out" ? [["expense", "Expense"], ["drawing", "Drawing"], ["supplier", "Paid to supplier"], ["other", "Other"]]
              : [["topup", "Owner top-up"], ["change", "Change from bank"], ["other", "Other"]] as [CashPurpose, string][]).map(([p, l]) => (
              <button key={p} type="button" role="radio" aria-checked={purpose === p} className={`choice ${purpose === p ? "on" : ""}`} onClick={() => pickPurpose(p as CashPurpose)}>{l}</button>
            ))}
          </div>
          {purpose === "expense" && (
            <div className="cm-field">
              <span className="small muted">What for</span>
              <div className="chips">
                {tips.categories.map((c) => <button key={c} type="button" className={`choice ${category === c ? "on" : ""}`} onClick={() => setCategory(c)}>{c}</button>)}
              </div>
              {(category === "Other" || (category && !tips.categories.includes(category))) && (
                <Input placeholder="Type the category, e.g. Printer paper" aria-label="Expense category" value={category === "Other" ? "" : category} onChange={(e) => setCategory(e.target.value)} />
              )}
            </div>
          )}
          {purpose === "drawing" && (
            <div className="cm-field">
              <div className="seg cm-handed">
                <button type="button" className={handed === "owner" ? "active" : ""} onClick={() => { setHanded("owner"); setPaidTo(""); }}>Given to owner</button>
                <button type="button" className={handed === "bank" ? "active" : ""} onClick={() => { setHanded("bank"); setPaidTo(""); }}>Deposited in bank</button>
              </div>
              <Input placeholder={handed === "owner" ? "Who took it, e.g. Vineet" : "Which bank, e.g. HDFC current account"} aria-label={handed === "owner" ? "Owner's name" : "Bank"}
                value={paidTo} onChange={(e) => setPaidTo(e.target.value)} />
            </div>
          )}
          {purpose === "supplier" && (
            <div className="cm-field">
              <Input placeholder="Supplier's name, e.g. Saras Dairy" aria-label="Supplier" value={paidTo} onChange={(e) => setPaidTo(e.target.value)} />
            </div>
          )}
          {names.length > 0 && !paidTo && (
            <div className="chips">{names.map((n) => <button key={n} type="button" className="choice small" onClick={() => setPaidTo(n)}>{n}</button>)}</div>
          )}
          <div className="shift-open-head">
            <span className="small muted">Amount</span>
            <button type="button" className="link" onClick={() => setMoveByNote(!moveByNote)}>{moveByNote ? "Just type the total" : "Count note by note"}</button>
          </div>
          {moveByNote ? <CashCount value={moveCounts} onChange={setMoveCounts} />
            : <Input inputMode="decimal" placeholder="₹" aria-label="Amount" value={amount} onChange={(e) => setAmount(e.target.value)} />}
          <Input placeholder={purpose === "other" ? (kind === "out" ? "What was it for? e.g. tip to delivery boy" : "Where did it come from?") : "Note (optional)"}
            aria-label={purpose === "other" ? "Reason" : "Note"} value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="row">
            <Button disabled={busy || !ready}>Save {saveLabel} of {formatPaise(moveTotal)}</Button>
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
