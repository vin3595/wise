import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import { formatPaise } from "../money";
import { useSession } from "../session";
import type { Outlet } from "../types";
import { Button, Card, ErrorText, Input, Select, useAction } from "../ui";

interface Check { key: string; block: boolean; text: string; fix: string }
interface Summary {
  bills: number; gross_paise: number; discount_paise: number; taxable_paise: number; gst_paise: number; round_off_paise: number; sales_paise: number;
  by_mode: Record<string, number>; cancelled_bills: number; cancelled_paise: number; corrections: number; first_bill: string | null; last_bill: string | null;
  top_items: { item: string; qty: number; sales_paise: number }[];
  shifts: { cashier: string; status: string; opening_paise: number; expected_paise: number | null; counted_paise: number | null; difference_paise: number | null; bills: number }[];
  cost_of_sales_paise?: number; food_cost_bp?: number | null; wastage_paise?: number; count_diff_paise?: number; purchases_paise: number; cash_out_paise: number;
}
interface DayStatus { day: string; today: string; closed: boolean; closed_at: string | null; closed_by: string | null; note: string | null; warnings: string[] | null; checks: Check[]; summary: Summary;
  recent: { day: string; status: string; sales_paise: number; closed_by: string | null }[] }

const MODE: Record<string, string> = { cash: "Cash", upi: "UPI", card: "Card", due: "Credit (due)", complimentary: "Complimentary" };
const fmtDay = (d: string) => new Date(d + "T12:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });

/** Day close: what's still open, the day's figures, and closing it. */
export default function DayClose() {
  const { me, deviceOutletId } = useSession();
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [outletId, setOutletId] = useState(me?.outlet_id || deviceOutletId || "");
  const [day, setDay] = useState("");
  const [st, setSt] = useState<DayStatus | null>(null);
  const [note, setNote] = useState("");
  const [anyway, setAnyway] = useState(false);
  const [reopen, setReopen] = useState("");
  const [printing, setPrinting] = useState(false);
  const { busy, error, run } = useAction();

  useEffect(() => { api<Outlet[]>("/tenant/outlets").then((o) => { setOutlets(o); setOutletId((c) => c || o[0]?.id || ""); }); }, []);
  const load = () => outletId && api<DayStatus>(`/day-close/${outletId}${day ? `?day=${day}` : ""}`).then((s) => { setSt(s); if (!day) setDay(s.day); });
  useEffect(() => { load(); }, [outletId, day]);
  useEffect(() => { if (!printing) return; const t = setTimeout(() => { window.print(); setPrinting(false); }, 300); return () => clearTimeout(t); }, [printing]);
  if (!st) return <p className="muted">Loading…</p>;
  const s = st.summary, outlet = outlets.find((o) => o.id === outletId);
  const blocking = st.checks.filter((c) => c.block);

  const close = () => run(async () => {
    const r = await api<DayStatus>("/day-close", { body: { outlet_id: outletId, day: st.day, note: note || null, close_anyway: anyway } });
    setSt(r); setNote(""); setAnyway(false);
  });

  return (
    <div className="stack dayclose">
      <div className="row">
        <h1 className="grow">Day close</h1>
        {!me?.outlet_id && outlets.length > 1 && <Select value={outletId} onChange={(e) => setOutletId(e.target.value)} aria-label="Outlet">{outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</Select>}
        <Input type="date" value={day} max={st.today} onChange={(e) => setDay(e.target.value)} aria-label="Business day" />
      </div>

      {st.closed ? (
        <div className="banner ok-banner">
          <strong>{fmtDay(st.day)} is closed</strong> by {st.closed_by} at {st.closed_at && new Date(st.closed_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}.
          {st.warnings && <> Closed with: {st.warnings.join("; ")}.</>}{st.note && <> Note: {st.note}</>}
        </div>
      ) : (
        <Card title={`Before closing ${fmtDay(st.day)}`}>
          {st.checks.length === 0 && <p className="ok">✓ Every shift is closed, every order is billed, stock is counted.</p>}
          <ul className="checklist">
            {st.checks.map((c) => <li key={c.key} className={c.block ? "block" : "warn"}><strong>{c.text}</strong><span>{c.fix}</span></li>)}
          </ul>
          {blocking.length > 0 && (
            <label className="toggle"><input type="checkbox" checked={anyway} onChange={(e) => setAnyway(e.target.checked)} /><span>Close anyway</span></label>
          )}
          {(anyway || st.checks.length > 0) && <Input placeholder={anyway ? "Why are you closing with these open? (required)" : "Note for the owner (optional)"} value={note} onChange={(e) => setNote(e.target.value)} />}
          <ErrorText error={error} />
          <div className="row">
            <Button disabled={busy || (blocking.length > 0 && (!anyway || note.trim().length < 5))} onClick={close}>Close {fmtDay(st.day)}</Button>
            <span className="muted small">After closing, no new bills can be made for this day. The owner's day summary SMS goes out.</span>
          </div>
        </Card>
      )}

      <div className="stats">
        <div className="stat"><span>Net sales</span><strong>{formatPaise(s.sales_paise)}</strong></div>
        <div className="stat"><span>Bills</span><strong>{s.bills}</strong></div>
        <div className="stat"><span>Average bill</span><strong>{formatPaise(s.bills ? Math.round(s.sales_paise / s.bills) : 0)}</strong></div>
        <div className="stat"><span>GST</span><strong>{formatPaise(s.gst_paise)}</strong></div>
        {s.food_cost_bp != null && <div className="stat"><span>Food cost</span><strong>{(s.food_cost_bp / 100).toFixed(1)}%</strong></div>}
      </div>
      <div className="grid2 wide">
        <Card title="Money in">
          <table className="table"><tbody>
            {Object.entries(s.by_mode).map(([m, v]) => <tr key={m}><td>{MODE[m] || m}</td><td className="num">{formatPaise(v)}</td></tr>)}
            {s.cash_out_paise > 0 && <tr><td>Cash paid out (expenses)</td><td className="num">−{formatPaise(s.cash_out_paise)}</td></tr>}
            <tr><td>Discounts given</td><td className="num">{formatPaise(s.discount_paise)}</td></tr>
            <tr><td>Cancelled bills</td><td className="num">{s.cancelled_bills} · {formatPaise(s.cancelled_paise)}</td></tr>
            <tr><td>Bills corrected</td><td className="num">{s.corrections}</td></tr>
            {s.first_bill && <tr><td>Bill numbers</td><td className="num small">{s.first_bill} to {s.last_bill}</td></tr>}
          </tbody></table>
        </Card>
        <Card title="Cash by shift">
          <table className="table">
            <thead><tr><th>Cashier</th><th className="num">Expected</th><th className="num">Counted</th><th className="num">Difference</th></tr></thead>
            <tbody>
              {s.shifts.map((x, i) => <tr key={i}><td>{x.cashier}{x.status === "open" && <span className="badge">open</span>}</td><td className="num">{x.expected_paise == null ? "–" : formatPaise(x.expected_paise)}</td>
                <td className="num">{x.counted_paise == null ? "–" : formatPaise(x.counted_paise)}</td><td className={`num ${x.difference_paise ? "error" : ""}`}>{x.difference_paise == null ? "–" : formatPaise(x.difference_paise)}</td></tr>)}
              {s.shifts.length === 0 && <tr><td colSpan={4} className="muted">No shifts.</td></tr>}
            </tbody>
          </table>
        </Card>
        <Card title="Best sellers">
          <table className="table"><tbody>{s.top_items.map((t) => <tr key={t.item}><td>{t.item}</td><td className="num">{t.qty}</td><td className="num">{formatPaise(t.sales_paise)}</td></tr>)}
            {s.top_items.length === 0 && <tr><td className="muted">No sales.</td></tr>}</tbody></table>
        </Card>
        {s.cost_of_sales_paise != null && (
          <Card title="Stock">
            <table className="table"><tbody>
              <tr><td>Cost of what was sold</td><td className="num">{formatPaise(s.cost_of_sales_paise)}</td></tr>
              <tr><td>Wastage</td><td className="num">{formatPaise(s.wastage_paise ?? 0)}</td></tr>
              <tr><td>Count differences</td><td className={`num ${(s.count_diff_paise ?? 0) < 0 ? "error" : ""}`}>{formatPaise(s.count_diff_paise ?? 0)}</td></tr>
              <tr><td>Purchases entered</td><td className="num">{formatPaise(s.purchases_paise)}</td></tr>
            </tbody></table>
          </Card>
        )}
      </div>
      <div className="row">
        <Button variant="ghost" onClick={() => setPrinting(true)}>Print day report</Button>
        {st.closed && <>
          <Input placeholder="Reason to reopen, e.g. a bill was missed" value={reopen} onChange={(e) => setReopen(e.target.value)} />
          <Button variant="ghost" disabled={busy || reopen.trim().length < 3} onClick={() => run(async () => { setSt(await api<DayStatus>("/day-close/reopen", { body: { outlet_id: outletId, day: st.day, reason: reopen } })); setReopen(""); })}>Reopen the day</Button>
        </>}
      </div>
      {st.recent.length > 0 && (
        <Card title="Recent days">
          <table className="table"><tbody>{st.recent.map((r) => <tr key={r.day}><td><button className="link" onClick={() => setDay(r.day)}>{fmtDay(r.day)}</button></td><td>{r.status === "closed" ? `Closed by ${r.closed_by}` : "Reopened"}</td><td className="num">{formatPaise(r.sales_paise || 0)}</td></tr>)}</tbody></table>
        </Card>
      )}
      {printing && createPortal(
        <div id="print-root"><div className="receipt">
          <div className="r-center"><strong>{outlet?.name}</strong></div>
          <div className="r-center">Day report · {fmtDay(st.day)}{st.closed ? "" : " (not closed)"}</div><hr />
          <div className="r-row"><span>Bills</span><span>{s.bills}</span></div>
          <div className="r-row"><span>Gross</span><span>{formatPaise(s.gross_paise)}</span></div>
          <div className="r-row"><span>Discounts</span><span>{formatPaise(s.discount_paise)}</span></div>
          <div className="r-row"><span>Taxable</span><span>{formatPaise(s.taxable_paise)}</span></div>
          <div className="r-row"><span>GST</span><span>{formatPaise(s.gst_paise)}</span></div>
          <div className="r-row r-total"><span>NET SALES</span><span>{formatPaise(s.sales_paise)}</span></div><hr />
          {Object.entries(s.by_mode).map(([m, v]) => <div key={m} className="r-row"><span>{MODE[m] || m}</span><span>{formatPaise(v)}</span></div>)}
          <div className="r-row"><span>Cancelled</span><span>{s.cancelled_bills} · {formatPaise(s.cancelled_paise)}</span></div><hr />
          {s.shifts.map((x, i) => <div key={i} className="r-row"><span>{x.cashier}</span><span>{x.difference_paise == null ? "open" : `diff ${formatPaise(x.difference_paise)}`}</span></div>)}
          {s.first_bill && <div className="r-center r-sub">{s.first_bill} – {s.last_bill}</div>}
        </div></div>, document.body)}
    </div>
  );
}
