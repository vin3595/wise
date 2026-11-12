import { useEffect, useState } from "react";
import { api } from "../api";
import { formatPaise } from "../money";
import { useSession } from "../session";
import type { Bill, BillRevision, Outlet } from "../types";
import { Card, ErrorText, Input, Select } from "../ui";
import ApprovalDialog from "../components/ApprovalDialog";
import BillCorrections from "../components/BillCorrections";
import { PrintBill } from "../components/Receipt";

const MODE: Record<string, string> = { cash: "Cash", upi: "UPI", card: "Card", due: "Credit", complimentary: "Comp" };
const CHANGE: Record<string, string> = { items: "items", payment: "payment", customer: "customer details" };

/** Bills for a day (or a search): reprint, correct wrong entries (one or many), cancel. */
export default function Bills() {
  const { me, deviceOutletId, can } = useSession();
  const isManager = can("approve");
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [outletId, setOutletId] = useState(me?.outlet_id || deviceOutletId || "");
  const [day, setDay] = useState("");
  const [q, setQ] = useState("");
  const [bills, setBills] = useState<Bill[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [printing, setPrinting] = useState<Bill | null>(null);
  const [cancelling, setCancelling] = useState<Bill | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [fixing, setFixing] = useState<Bill[] | null>(null);
  const [history, setHistory] = useState<{ bill: Bill; revs: BillRevision[] } | null>(null);
  const [lock, setLock] = useState<string>("");
  const [sortBy, setSortBy] = useState("new");
  const [smsFor, setSmsFor] = useState<Bill | null>(null);
  const [smsTo, setSmsTo] = useState("");
  const [smsNote, setSmsNote] = useState<string | null>(null);

  useEffect(() => { api<Outlet[]>("/tenant/outlets").then((o) => { setOutlets(o); setOutletId((c) => c || o[0]?.id || ""); }); }, []);
  useEffect(() => { api<{ through: string | null }>("/tenant/bill-lock").then((l) => setLock(l.through || "")).catch(() => {}); }, []);
  const load = () => {
    if (!outletId) return;
    const params = new URLSearchParams({ outlet_id: outletId });
    if (q.trim()) params.set("q", q.trim());
    else if (day) params.set("day", day);
    api<Bill[]>(`/bills?${params}`).then((b) => { setBills(b); setPicked([]); }).catch((e) => setError(e.message));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [outletId, day, q]);

  const paid = bills.filter((b) => b.status === "paid");
  const SORTS: Record<string, (a: Bill, b: Bill) => number> = {
    new: (a, b) => b.created_at.localeCompare(a.created_at),
    old: (a, b) => a.created_at.localeCompare(b.created_at),
    high: (a, b) => b.total_paise - a.total_paise,
    low: (a, b) => a.total_paise - b.total_paise,
    token: (a, b) => (a.token_no || 0) - (b.token_no || 0),
    mode: (a, b) => (a.payments[0]?.mode || "").localeCompare(b.payments[0]?.mode || ""),
  };
  const sorted = [...bills].sort(SORTS[sortBy]);
  const editable = paid.filter((b) => !b.locked);
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < 50 ? [...p, id] : p));
  const openHistory = async (b: Bill) => setHistory({ bill: b, revs: await api<BillRevision[]>(`/bills/${b.id}/history`) });

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Bills</h1>
        <div className="row">
          {!me?.outlet_id && outlets.length > 1 && (
            <Select value={outletId} onChange={(e) => setOutletId(e.target.value)}>{outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</Select>
          )}
          <Input type="date" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Day" />
          <Input placeholder="Bill no. or mobile" value={q} onChange={(e) => setQ(e.target.value)} />
          <Select value={sortBy} onChange={(e) => setSortBy(e.target.value)} aria-label="Sort bills">
            <option value="new">Newest first</option>
            <option value="old">Oldest first</option>
            <option value="high">Highest amount first</option>
            <option value="low">Lowest amount first</option>
            <option value="token">By token</option>
            <option value="mode">By payment mode</option>
          </Select>
        </div>
      </div>
      <p className="muted">{paid.length} bills, {formatPaise(paid.reduce((s, b) => s + b.total_paise, 0))}. Tick bills to correct wrong entries; a corrected bill keeps its number and its earlier version.</p>
      <ErrorText error={error} />

      {picked.length > 0 && (
        <div className="select-bar">
          <strong>{picked.length} selected</strong>
          <button className="btn btn-primary" onClick={() => setFixing(bills.filter((b) => picked.includes(b.id)))}>Correct {picked.length === 1 ? "bill" : `${picked.length} bills`}</button>
          <button className="link" onClick={() => setPicked([])}>Clear</button>
          {picked.length === 50 && <span className="muted small">50 at a time</span>}
        </div>
      )}

      <Card>
        <table className="table">
          <thead><tr>
            <th className="pick"><input type="checkbox" aria-label="Select all" checked={editable.length > 0 && editable.slice(0, 50).every((b) => picked.includes(b.id))}
              onChange={(e) => setPicked(e.target.checked ? editable.slice(0, 50).map((b) => b.id) : [])} /></th>
            <th>Bill no.</th><th>Time</th><th>Token</th><th>Customer</th><th>Paid by</th><th className="num">Total</th><th />
          </tr></thead>
          <tbody>
            {sorted.map((b) => (
              <tr key={b.id} className={`${b.status === "cancelled" ? "inactive" : ""} ${picked.includes(b.id) ? "picked" : ""}`}>
                <td className="pick">{b.status === "paid" && !b.locked && <input type="checkbox" aria-label={`Select ${b.invoice_no}`} checked={picked.includes(b.id)} onChange={() => toggle(b.id)} />}</td>
                <td>{b.invoice_no}{b.status === "cancelled" && <span className="badge">cancelled</span>}
                  {(b.revision || 1) > 1 && <button className="badge edited" onClick={() => openHistory(b)}>corrected</button>}
                  {b.locked && <span className="badge">locked</span>}</td>
                <td>{new Date(b.created_at).toLocaleTimeString("en-IN", { timeStyle: "short" })}</td>
                <td>#{b.token_no}</td>
                <td>{b.customer_phone || b.customer_name || "—"}</td>
                <td>{b.payments.map((p) => MODE[p.mode]).join(" + ")}</td>
                <td className="num">{formatPaise(b.total_paise)}</td>
                <td className="actions">
                  <button className="link" onClick={() => setPrinting(b)}>Reprint</button>
                  {b.status === "paid" && <button className="link" onClick={() => { setSmsFor(b); setSmsTo(b.customer_phone || ""); setSmsNote(null); }}>SMS bill</button>}
                  {b.status === "paid" && !b.locked && <button className="link" onClick={() => setFixing([b])}>Correct</button>}
                  {b.status === "paid" && <button className="link danger" onClick={() => setCancelling(b)}>Cancel</button>}
                </td>
              </tr>
            ))}
            {bills.length === 0 && <tr><td colSpan={8} className="muted">No bills.</td></tr>}
          </tbody>
        </table>
      </Card>

      {can("tenant.settings") && (
        <Card title="Lock filed periods">
          <p className="muted">After your GST return for a month is filed, lock its bills so nobody can correct them any more.</p>
          <div className="row">
            <span>Lock bills up to</span>
            <Input type="date" value={lock} onChange={(e) => setLock(e.target.value)} />
            <button className="btn btn-ghost" onClick={async () => {
              try { await api("/tenant/bill-lock", { method: "PUT", body: { through: lock || null } }); load(); setError(null); }
              catch (e) { setError(e instanceof Error ? e.message : String(e)); }
            }}>Save lock date</button>
          </div>
        </Card>
      )}

      {printing && <PrintBill bill={printing} onDone={() => setPrinting(null)} />}
      {smsFor && (
        <div className="modal-back" onClick={() => setSmsFor(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>SMS bill {smsFor.invoice_no}</h2>
            <p className="muted">The customer gets a link to this bill ({formatPaise(smsFor.total_paise)}).</p>
            <Input inputMode="tel" placeholder="Customer's mobile" value={smsTo} onChange={(e) => setSmsTo(e.target.value)} />
            {smsNote && <p className={smsNote.startsWith("Sent") ? "ok" : "error"}>{smsNote}</p>}
            <div className="row">
              <button className="btn btn-primary" disabled={!smsTo} onClick={async () => {
                try { await api(`/bills/${smsFor.id}/sms`, { body: { phone: smsTo } }); setSmsNote("Sent. It goes out within a few seconds (or when the internet is back)."); }
                catch (e) { setSmsNote(e instanceof Error ? e.message : String(e)); }
              }}>Send SMS</button>
              <button className="btn btn-ghost" onClick={() => setSmsFor(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
      {cancelling && (
        <ApprovalDialog title={`Cancel bill ${cancelling.invoice_no} (${formatPaise(cancelling.total_paise)})?`} onClose={() => setCancelling(null)}
          onSubmit={async (reason, pin) => { await api(`/bills/${cancelling.id}/cancel`, { body: { reason, manager_pin: pin } }); load(); }} />
      )}
      {fixing && <BillCorrections bills={fixing} outletId={outletId} isManager={isManager} onClose={() => setFixing(null)} onSaved={load} />}
      {history && (
        <div className="modal-back" onClick={() => setHistory(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>{history.bill.invoice_no}: earlier versions</h2>
            <ol className="history">
              {history.revs.map((r) => (
                <li key={r.revision_no}>
                  <strong>{formatPaise(r.old_total_paise)} → {formatPaise(r.new_total_paise)}</strong>
                  <span>Changed {r.changes.map((c) => CHANGE[c] || c).join(", ")}: {r.reason}</span>
                  <span className="muted small">{new Date(r.created_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}, by {r.edited_by_name}{r.approved_by_name ? `, approved by ${r.approved_by_name}` : ""}</span>
                  <span className="muted small">Before: {r.before.lines.map((l) => `${l.qty} × ${l.item_name}`).join(", ")}; {r.before.payments.map((p) => MODE[p.mode]).join(" + ")}</span>
                </li>
              ))}
            </ol>
            <button className="btn btn-ghost" onClick={() => setHistory(null)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}
