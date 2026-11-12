import { useEffect, useState } from "react";
import { api } from "../api";
import { usePolling } from "../hooks";
import { formatPaise } from "../money";
import { useSession } from "../session";
import type { Outlet, Shift } from "../types";
import { Button, Card, ErrorText, Input, Select, useAction } from "../ui";

interface Drawer { id: string; name: string; is_active: boolean; in_use_by: string | null }
const time = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : "");
const denomText = (d: Record<string, number> | null | undefined) => (d ? Object.entries(d).filter(([, n]) => n).sort((a, b) => Number(b[0].slice(1)) - Number(a[0].slice(1)))
  .map(([k, n]) => `₹${k.slice(1)}${k[0] === "c" ? " coin" : ""} × ${n}`).join(", ") : "");
const today = () => { const d = new Date(Date.now() + (330 - 240) * 60000); return d.toISOString().slice(0, 10); };

/** Every cashier's shift at once: drawer, opening, cash sales, cash in / out, what should be in the drawer. */
export default function Cash() {
  const { me, deviceOutletId, can } = useSession();
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [outletId, setOutletId] = useState(me?.outlet_id || deviceOutletId || "");
  const [day, setDay] = useState(today());
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [drawers, setDrawers] = useState<Drawer[]>([]);
  const [newDrawer, setNewDrawer] = useState("");
  const { busy, error, run } = useAction();
  useEffect(() => { api<Outlet[]>("/tenant/outlets").then((o) => { setOutlets(o); setOutletId((c) => c || o[0]?.id || ""); }); }, []);
  usePolling(async () => {
    if (!outletId) return;
    setShifts(await api<Shift[]>(`/shifts?outlet_id=${outletId}&day=${day}`));
    setDrawers(await api<Drawer[]>(`/outlets/${outletId}/drawers`));
  }, 8000, [outletId, day], { outletId, topics: ["shift", "cash", "bills"] });

  const sum = (f: (s: Shift) => number | null | undefined) => shifts.reduce((t, s) => t + (f(s) ?? 0), 0);
  const open = shifts.filter((s) => s.status === "open");
  const moves = shifts.flatMap((s) => (s.movements || []).map((m) => ({ ...m, cashier: s.opened_by_name, drawer: s.drawer_name }))).sort((a, b) => (b.at || "").localeCompare(a.at || ""));
  return (
    <div className="stack">
      <div className="row">
        <h1 className="grow">Cash</h1>
        {!me?.outlet_id && outlets.length > 1 && <Select value={outletId} onChange={(e) => setOutletId(e.target.value)} aria-label="Outlet">{outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</Select>}
        <Input type="date" value={day} max={today()} onChange={(e) => setDay(e.target.value)} aria-label="Day" />
      </div>
      <div className="stats">
        <div className="stat"><span>Shifts open now</span><strong>{open.length}</strong></div>
        <div className="stat"><span>Opening cash</span><strong>{formatPaise(sum((s) => s.opening_cash_paise))}</strong></div>
        <div className="stat"><span>Cash sales</span><strong>{formatPaise(sum((s) => s.cash_sales_paise))}</strong></div>
        <div className="stat"><span>Cash in / out</span><strong>+{formatPaise(sum((s) => s.cash_in_paise))} / −{formatPaise(sum((s) => s.cash_out_paise))}</strong></div>
        <div className="stat"><span>Should be in drawers</span><strong>{formatPaise(sum((s) => (s.status === "open" ? s.expected_cash_paise : s.counted_cash_paise ?? s.expected_cash_paise)))}</strong></div>
        <div className="stat"><span>Differences at close</span><strong className={sum((s) => s.variance_paise) ? "error" : "ok"}>{formatPaise(sum((s) => s.variance_paise))}</strong></div>
      </div>
      <div className="shift-cards">
        {shifts.map((s) => (
          <Card key={s.id} title={`${s.opened_by_name ?? "?"} · ${s.drawer_name ?? "Drawer"}`} actions={<span className={`status-pill ${s.status === "open" ? "st-preparing" : "st-completed"}`}>{s.status === "open" ? "Open" : "Closed"}</span>}>
            <p className="muted small">Opened {time(s.opened_at)}{s.closed_at && <> · closed {time(s.closed_at)}{s.closed_by_name && s.closed_by_name !== s.opened_by_name ? ` by ${s.closed_by_name}` : ""}</>} · {s.bills} bills · sales {formatPaise(s.sales_paise)}</p>
            <table className="table"><tbody>
              <tr><td>Opening cash{s.opening_denoms && <div className="muted small">{denomText(s.opening_denoms)}</div>}</td><td className="num">{formatPaise(s.opening_cash_paise)}</td></tr>
              {s.expected_opening_paise != null && s.expected_opening_paise !== s.opening_cash_paise && <tr><td className="small">Last close left</td><td className="num small error">{formatPaise(s.expected_opening_paise)}</td></tr>}
              <tr><td>+ Cash sales</td><td className="num">{formatPaise(s.cash_sales_paise ?? 0)}</td></tr>
              <tr><td>+ Cash in</td><td className="num">{formatPaise(s.cash_in_paise)}</td></tr>
              <tr><td>− Cash out (expenses)</td><td className="num">{formatPaise(s.cash_out_paise)}</td></tr>
              <tr className="sum"><td><strong>Should be in the drawer</strong></td><td className="num"><strong>{formatPaise(s.expected_cash_paise ?? 0)}</strong></td></tr>
              {s.status === "closed" && <>
                <tr><td>Counted{s.counted_denoms && <div className="muted small">{denomText(s.counted_denoms)}</div>}</td><td className="num">{formatPaise(s.counted_cash_paise ?? 0)}</td></tr>
                <tr><td>Difference</td><td className={`num ${s.variance_paise ? "error" : "ok"}`}>{formatPaise(s.variance_paise ?? 0)}</td></tr>
              </>}
              {Object.entries(s.by_mode).filter(([m]) => m !== "cash").map(([m, v]) => <tr key={m} className="muted"><td className="small">{m.toUpperCase()} (not in drawer)</td><td className="num small">{formatPaise(v)}</td></tr>)}
            </tbody></table>
            {(s.movements || []).length > 0 && <ul className="move-list">
              {s.movements!.map((m, i) => <li key={i}><span className={m.kind === "out" ? "error" : "ok"}>{m.kind === "out" ? "−" : "+"}{formatPaise(m.amount_paise)}</span> {m.reason} <span className="muted small">· {time(m.at)}{m.denoms ? ` · ${denomText(m.denoms)}` : ""}</span></li>)}
            </ul>}
          </Card>
        ))}
        {shifts.length === 0 && <Card><p className="muted">No shifts on this day.</p></Card>}
      </div>
      {moves.length > 0 && (
        <Card title={`All cash in / out (${moves.length})`}>
          <table className="table">
            <thead><tr><th>Time</th><th>Cashier</th><th>Drawer</th><th>Reason</th><th>Notes and coins</th><th className="num">Amount</th></tr></thead>
            <tbody>{moves.map((m, i) => <tr key={i}><td>{time(m.at)}</td><td>{m.cashier}</td><td>{m.drawer}</td><td>{m.reason}</td><td className="small">{denomText(m.denoms)}</td>
              <td className={`num ${m.kind === "out" ? "error" : "ok"}`}>{m.kind === "out" ? "−" : "+"}{formatPaise(m.amount_paise)}</td></tr>)}</tbody>
          </table>
        </Card>
      )}
      <Card title="Drawers">
        <p className="muted small">Each cashier's shift uses one drawer, and each drawer's next shift is checked against what its last shift left. A second cashier who starts while a drawer is in use gets a new drawer automatically; rename them to match your counters.</p>
        <table className="table"><tbody>
          {drawers.map((d) => <tr key={d.id} className={d.is_active ? "" : "muted"}><td>{d.name}</td><td>{d.in_use_by ? `In ${d.in_use_by}'s shift` : d.is_active ? "Free" : "Not in use"}</td>
            <td>{can("day.close") && <button className="link" onClick={() => { const n = prompt("Drawer name", d.name); if (n && n.trim()) run(async () => { await api(`/drawers/${d.id}`, { method: "PATCH", body: { name: n.trim(), is_active: d.is_active } }); setDrawers(await api<Drawer[]>(`/outlets/${outletId}/drawers`)); }); }}>Rename</button>}</td></tr>)}
        </tbody></table>
        {can("day.close") && <div className="row">
          <Input placeholder="New drawer, e.g. Counter 2" value={newDrawer} onChange={(e) => setNewDrawer(e.target.value)} />
          <Button variant="ghost" disabled={busy || !newDrawer.trim()} onClick={() => run(async () => { await api(`/outlets/${outletId}/drawers`, { body: { name: newDrawer.trim() } }); setNewDrawer(""); setDrawers(await api<Drawer[]>(`/outlets/${outletId}/drawers`)); })}>Add drawer</Button>
        </div>}
        <ErrorText error={error} />
      </Card>
    </div>
  );
}
