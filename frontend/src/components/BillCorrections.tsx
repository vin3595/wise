import { useEffect, useMemo, useRef, useState } from "react";
import { api, newId } from "../api";
import { formatPaise, inputToPaise, paiseToInput } from "../money";
import type { Bill, BillFixResult, OutletMenu, PayMode } from "../types";
import { Button, ErrorText, Input } from "../ui";

type Add = { key: string; variant_id: string; label: string; unit: number; qty: number };
type Draft = {
  qty: Record<string, number>;          // bill line id -> corrected qty
  adds: Add[];
  mode: PayMode | null;                 // single-payment bills: new mode
  discount: string | null;              // ₹ text; null = unchanged
  discountReason: string;
  name: string; phone: string; gstin: string;
};
const MODES: { m: PayMode; label: string }[] = [{ m: "cash", label: "Cash" }, { m: "upi", label: "UPI" }, { m: "card", label: "Card" }];
const PAY_LABEL: Record<string, string> = { cash: "Cash", upi: "UPI", card: "Card", due: "Credit", complimentary: "Complimentary" };

const fresh = (b: Bill): Draft => ({
  qty: Object.fromEntries(b.lines.map((l) => [l.id!, l.qty])), adds: [], mode: null, discount: null,
  discountReason: b.discount_reason || "", name: b.customer_name || "", phone: b.customer_phone || "",
  gstin: b.customer_gstin || "",
});
const single = (b: Bill) => b.payments.length === 1 && ["cash", "upi", "card"].includes(b.payments[0].mode);

/** What the server needs for one bill, or null when nothing on it was touched. */
function toFix(b: Bill, d: Draft) {
  const lines = b.lines.filter((l) => d.qty[l.id!] !== l.qty).map((l) => ({ line_id: l.id!, qty: d.qty[l.id!] }));
  const customerChanged = d.name !== (b.customer_name || "") || d.phone !== (b.customer_phone || "") ||
    d.gstin !== (b.customer_gstin || "");
  const disc = d.discount === null ? null : inputToPaise(d.discount) ?? 0;
  const discChanged = disc !== null && disc !== b.discount_paise;
  const modeChanged = d.mode !== null && d.mode !== b.payments[0]?.mode;
  if (!lines.length && !d.adds.length && !customerChanged && !discChanged && !modeChanged) return null;
  return {
    bill_id: b.id, revision: b.revision || 1, lines,
    add_items: d.adds.map((a) => ({ variant_id: a.variant_id, qty: a.qty })),
    ...(customerChanged ? { customer: { name: d.name || null, phone: d.phone || null, gstin: d.gstin || null, declined: !d.phone } } : {}),
    ...(discChanged ? { discount: { kind: "flat", value: disc, reason: d.discountReason || null } } : {}),
    ...(modeChanged || (single(b) && (lines.length || d.adds.length || discChanged)) ? { payment_mode: d.mode || b.payments[0].mode } : {}),
  };
}

/** All selected bills on one screen. Correct each, then Save all: every bill is saved, or none is. */
export default function BillCorrections({ bills, outletId, isManager, onClose, onSaved }: {
  bills: Bill[]; outletId: string; isManager: boolean; onClose: () => void; onSaved: () => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() => Object.fromEntries(bills.map((b) => [b.id, fresh(b)])));
  const [menu, setMenu] = useState<OutletMenu | null>(null);
  const [results, setResults] = useState<Record<string, BillFixResult>>({});
  const [needsPin, setNeedsPin] = useState(false);
  const [reason, setReason] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<number | null>(null);
  const seq = useRef(0);

  useEffect(() => { api<OutletMenu>(`/menu/outlets/${outletId}`).then(setMenu).catch(() => setMenu(null)); }, [outletId]);
  const fixes = useMemo(() => bills.map((b) => toFix(b, drafts[b.id])).filter(Boolean), [bills, drafts]);

  // Live preview of the new totals (nothing is saved).
  useEffect(() => {
    if (!fixes.length) { setResults({}); setNeedsPin(false); return; }
    const n = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const r = await api<{ results: BillFixResult[]; needs_approval: boolean }>("/bills/corrections",
          { body: { reason: reason.trim().length >= 3 ? reason : "preview", bills: fixes, dry_run: true } });
        if (n !== seq.current) return;
        setResults(Object.fromEntries(r.results.map((x) => [x.bill_id, x])));
        setNeedsPin(r.needs_approval && !isManager);
        setError(null);
      } catch (e) { if (n === seq.current) setError(e instanceof Error ? e.message : String(e)); }
    }, 350);
    return () => clearTimeout(t);
  }, [JSON.stringify(fixes)]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (id: string, patch: Partial<Draft>) => setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
  const before = bills.filter((b) => toFix(b, drafts[b.id])).reduce((s, b) => s + b.total_paise, 0);
  const after = bills.filter((b) => toFix(b, drafts[b.id])).reduce((s, b) => s + (results[b.id]?.new_total_paise ?? b.total_paise), 0);
  const bad = Object.values(results).filter((r) => !r.ok);
  const allMode = (m: PayMode) => setDrafts((d) => Object.fromEntries(bills.map((b) => [b.id, single(b) ? { ...d[b.id], mode: m } : d[b.id]])));

  async function saveAll() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ saved: boolean; results: BillFixResult[] }>("/bills/corrections",
        { body: { reason: reason.trim(), manager_pin: pin || null, bills: fixes } });
      setResults(Object.fromEntries(r.results.map((x) => [x.bill_id, x])));
      if (r.saved) { setDone(r.results.length); onSaved(); }
      else setError("Nothing was saved. Fix the bills marked in red, then save again.");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    setBusy(false);
  }

  if (done !== null) {
    return (
      <div className="modal-back"><div className="modal">
        <h2>{done} {done === 1 ? "bill" : "bills"} corrected</h2>
        <p className="muted">Same bill numbers. The earlier versions are kept under each bill's history and in Reports, Bill edits.</p>
        <Button onClick={onClose}>Done</Button>
      </div></div>
    );
  }

  return (
    <div className="fix-screen" role="dialog" aria-label="Correct bills">
      <header className="fix-head">
        <div className="grow">
          <h1>Correct {bills.length} {bills.length === 1 ? "bill" : "bills"}</h1>
          <p className="muted">Change what was entered wrong. Bill numbers stay the same and each bill keeps its earlier version.</p>
        </div>
        <button className="btn btn-ghost" onClick={onClose}>Close without saving</button>
      </header>

      <div className="fix-tools">
        <span className="muted">Paid by, on every bill paid one way:</span>
        {MODES.map((x) => <button key={x.m} className="choice" onClick={() => allMode(x.m)}>{x.label}</button>)}
      </div>

      <div className="fix-list">
        {bills.map((b) => (
          <FixCard key={b.id} bill={b} draft={drafts[b.id]} menu={menu} result={results[b.id]}
            changed={!!toFix(b, drafts[b.id])} onChange={(p) => set(b.id, p)} onReset={() => set(b.id, fresh(b))} />
        ))}
      </div>

      <footer className="fix-foot">
        <div className="fix-sum">
          <strong>{fixes.length} of {bills.length} changed</strong>
          {fixes.length > 0 && <span>Total {formatPaise(before)} → {formatPaise(after)}{after !== before && ` (${after > before ? "+" : "−"}${formatPaise(Math.abs(after - before))})`}</span>}
          {bad.length > 0 && <span className="error">{bad.length} {bad.length === 1 ? "bill needs" : "bills need"} fixing first</span>}
        </div>
        <Input className="input fix-reason" placeholder="Reason, e.g. Wrong items punched at rush hour" value={reason} onChange={(e) => setReason(e.target.value)} />
        {needsPin && <Input className="input fix-pin" type="password" inputMode="numeric" maxLength={4} placeholder="Manager PIN" value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} />}
        <Button disabled={busy || !fixes.length || reason.trim().length < 3 || bad.length > 0 || (needsPin && pin.length !== 4)} onClick={saveAll}>
          {busy ? "Saving…" : `Save all (${fixes.length})`}
        </Button>
        <ErrorText error={error} />
      </footer>
    </div>
  );
}

function FixCard({ bill: b, draft: d, menu, result, changed, onChange, onReset }: {
  bill: Bill; draft: Draft; menu: OutletMenu | null; result?: BillFixResult; changed: boolean;
  onChange: (p: Partial<Draft>) => void; onReset: () => void;
}) {
  const [find, setFind] = useState("");
  const matches = useMemo(() => {
    const q = find.trim().toLowerCase();
    if (!menu || q.length < 2) return [];
    return menu.items.flatMap((it) => it.variants.filter((v) => v.available).map((v) => ({ it, v })))
      .filter(({ it }) => it.name.toLowerCase().includes(q) || (it.short_code || "").toLowerCase() === q).slice(0, 6);
  }, [find, menu]);
  const newTotal = result?.ok ? result.new_total_paise : null;
  const mode = d.mode || b.payments[0]?.mode;

  return (
    <article className={`fix-card ${changed ? "changed" : ""} ${result && !result.ok ? "bad" : ""}`}>
      <header>
        <div className="grow">
          <strong>{b.invoice_no}</strong>
          <span className="muted"> Token #{b.token_no} · {new Date(b.created_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}</span>
        </div>
        <div className="fix-totals">
          {changed && newTotal !== null && newTotal !== b.total_paise ? (
            <><s>{formatPaise(b.total_paise)}</s><strong>{formatPaise(newTotal)}</strong></>
          ) : <strong>{formatPaise(b.total_paise)}</strong>}
        </div>
      </header>
      {result && !result.ok && <p className="error">{result.error}</p>}

      <ul className="fix-lines">
        {b.lines.map((l) => {
          const q = d.qty[l.id!];
          return (
            <li key={l.id} className={q === 0 ? "removed" : q !== l.qty ? "edited" : ""}>
              <span className="grow">{l.item_name}{l.variant_name !== "Regular" && <span className="muted"> {l.variant_name}</span>}
                {l.addons_text && <span className="muted small"> + {l.addons_text}</span>}</span>
              <span className="muted small">{formatPaise(l.unit_paise)}</span>
              <span className="qty-ctl">
                <button aria-label={`One less ${l.item_name}`} onClick={() => onChange({ qty: { ...d.qty, [l.id!]: Math.max(0, q - 1) } })}>−</button>
                <span>{q}</span>
                <button aria-label={`One more ${l.item_name}`} onClick={() => onChange({ qty: { ...d.qty, [l.id!]: q + 1 } })}>+</button>
              </span>
            </li>
          );
        })}
        {d.adds.map((a) => (
          <li key={a.key} className="added">
            <span className="grow">{a.label} <span className="badge">added</span></span>
            <span className="muted small">{formatPaise(a.unit)}</span>
            <span className="qty-ctl">
              <button aria-label={`One less ${a.label}`} onClick={() => onChange({ adds: d.adds.flatMap((x) => x.key !== a.key ? [x] : x.qty > 1 ? [{ ...x, qty: x.qty - 1 }] : []) })}>−</button>
              <span>{a.qty}</span>
              <button aria-label={`One more ${a.label}`} onClick={() => onChange({ adds: d.adds.map((x) => x.key === a.key ? { ...x, qty: x.qty + 1 } : x) })}>+</button>
            </span>
          </li>
        ))}
      </ul>
      <div className="fix-add">
        <Input placeholder="Add an item: type its name or code" value={find} onChange={(e) => setFind(e.target.value)} />
        {matches.length > 0 && (
          <div className="fix-matches">
            {matches.map(({ it, v }) => (
              <button key={v.variant_id} onClick={() => {
                onChange({ adds: [...d.adds, { key: newId(), variant_id: v.variant_id, label: `${it.name}${it.variants.length > 1 ? ` ${v.name}` : ""}`, unit: v.price_paise, qty: 1 }] });
                setFind("");
              }}>{it.name}{it.variants.length > 1 && ` ${v.name}`} <span className="muted">{formatPaise(v.price_paise)}</span></button>
            ))}
          </div>
        )}
      </div>

      <div className="fix-grid">
        <div>
          <span className="field-label">Paid by</span>
          {single(b) ? (
            <div className="fix-modes">
              {MODES.map((x) => <button key={x.m} className={`choice ${mode === x.m ? "on" : ""}`} onClick={() => onChange({ mode: x.m })}>{x.label}</button>)}
            </div>
          ) : <p className="muted small">{b.payments.map((p) => `${PAY_LABEL[p.mode]} ${formatPaise(p.amount_paise)}`).join(" + ")}. A split or credit bill is corrected by cancelling and billing again.</p>}
        </div>
        <label className="field">
          <span className="field-label">Discount ₹</span>
          <Input inputMode="decimal" value={d.discount ?? paiseToInput(b.discount_paise)} onChange={(e) => onChange({ discount: e.target.value })} />
        </label>
        {(d.discount !== null && (inputToPaise(d.discount) ?? 0) > 0) && (
          <label className="field"><span className="field-label">Discount reason</span>
            <Input value={d.discountReason} onChange={(e) => onChange({ discountReason: e.target.value })} /></label>
        )}
        <label className="field"><span className="field-label">Customer mobile</span>
          <Input inputMode="tel" value={d.phone} placeholder="Not given" onChange={(e) => onChange({ phone: e.target.value.replace(/[^\d+]/g, "") })} /></label>
        <label className="field"><span className="field-label">Name</span>
          <Input value={d.name} onChange={(e) => onChange({ name: e.target.value })} /></label>
        <label className="field"><span className="field-label">GSTIN (B2B)</span>
          <Input value={d.gstin} onChange={(e) => onChange({ gstin: e.target.value.toUpperCase() })} /></label>
      </div>
      {changed && (
        <footer>
          <span className="small muted">{result?.ok ? `Changing ${result.changes.join(", ")}${result.needs_approval.length ? ". Needs manager PIN" : ""}` : "Checking…"}</span>
          <button className="link" onClick={onReset}>Undo changes</button>
        </footer>
      )}
    </article>
  );
}
