import { useEffect, useState } from "react";
import { api } from "../api";
import { formatPaise, inputToPaise, paiseToInput } from "../money";
import { useSession } from "../session";
import { Card, ErrorText, Input } from "../ui";

interface Alert { id: string; outlet_name: string; kind: "open" | "close"; cashier_name: string | null; expected_paise: number;
  counted_paise: number; difference_paise: number; denoms: Record<string, number> | null; created_at: string }

const denomText = (d: Record<string, number> | null) => !d ? "Total typed in, not counted by note" :
  Object.entries(d).sort((a, b) => Number(b[0].slice(1)) - Number(a[0].slice(1)) || (a[0] < b[0] ? 1 : -1))
    .map(([k, n]) => `${n} × ₹${k.slice(1)}${k[0] === "c" ? " coin" : ""}`).join(", ");

/** Cash counts that didn't match what the system expected. Managers check each one and note why. */
export default function CashAlerts() {
  const { can } = useSession();
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [tol, setTol] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = () => api<Alert[]>("/cash-alerts").then(setAlerts).catch(() => setAlerts([]));
  useEffect(() => {
    load();
    api<{ tolerance_paise: number }>("/tenant/cash-tolerance").then((t) => setTol(paiseToInput(t.tolerance_paise))).catch(() => {});
  }, []);
  if (!can("reports.view") || alerts === null) return null;
  return (
    <Card title={alerts.length ? `Cash to check (${alerts.length})` : "Cash counts"}>
      {alerts.length === 0 && <p className="muted">Every cash count matched. Each shift opens with what the last close left in the drawer; if a cashier's count is different, it shows here and you get an SMS.</p>}
      <ul className="cash-alerts">
        {alerts.map((a) => (
          <li key={a.id} className={a.difference_paise < 0 ? "short" : "extra"}>
            <div className="ca-head">
              <strong>{formatPaise(Math.abs(a.difference_paise))} {a.difference_paise < 0 ? "short" : "extra"}</strong>
              <span>{a.cashier_name}, {a.kind === "open" ? "opening" : "closing"} count at {a.outlet_name}</span>
              <span className="muted small">{new Date(a.created_at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}</span>
            </div>
            <div className="small">Should be {formatPaise(a.expected_paise)}{a.kind === "open" ? " (left by the last close)" : ""}, counted {formatPaise(a.counted_paise)}.</div>
            <div className="small muted">{denomText(a.denoms)}</div>
            <div className="row">
              <Input placeholder="What happened? e.g. counted again, change given wrong" value={notes[a.id] || ""} onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })} />
              <button className="btn btn-ghost" disabled={(notes[a.id] || "").trim().length < 2} onClick={async () => {
                try { await api(`/cash-alerts/${a.id}/check`, { body: { note: notes[a.id] } }); load(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
              }}>Mark checked</button>
            </div>
          </li>
        ))}
      </ul>
      {can("tenant.settings") && (
        <div className="row small">
          <span>Don't alert for differences up to ₹</span>
          <Input className="input price" inputMode="decimal" value={tol} onChange={(e) => setTol(e.target.value)} />
          <button className="link" onClick={async () => {
            try { await api("/tenant/cash-tolerance", { method: "PUT", body: { tolerance_paise: inputToPaise(tol || "0") ?? 0 } }); setError(null); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
          }}>Save</button>
        </div>
      )}
      <ErrorText error={error} />
    </Card>
  );
}
