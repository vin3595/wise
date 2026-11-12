import { useState } from "react";
import { api } from "../api";
import { usePolling } from "../hooks";
import { Card } from "../ui";

interface Alert { id: string; kind: string; text: string; outlet: string | null; at: string; seen: boolean }
const KIND: Record<string, string> = { cancel: "Bill cancelled", discount: "Big discount", comp: "Complimentary", edit: "Bill corrected",
  cash: "Cash doesn't match", printer: "Printer", box: "Box was off" };

/** The last week's alerts for the owner/manager: the same ones that go by SMS. */
export default function OwnerAlerts({ quietWhenEmpty = true }: { quietWhenEmpty?: boolean }) {
  const [data, setData] = useState<{ unseen: number; alerts: Alert[] } | null>(null);
  usePolling(async () => setData(await api(`/alerts`)), 60000, [], { topics: ["alerts"] });
  if (!data) return null;
  if (data.alerts.length === 0) return quietWhenEmpty ? null : (
    <Card title="Alerts"><p className="muted">No alerts this week. Cancelled bills, big discounts, cash differences and printer problems show here.</p></Card>
  );
  return (
    <Card title={`Alerts${data.unseen ? ` (${data.unseen} new)` : ""}`}>
      <ul className="owner-alerts">
        {data.alerts.slice(0, 12).map((a) => (
          <li key={a.id} className={a.seen ? "" : "new"}>
            <span className={`alert-kind k-${a.kind}`}>{KIND[a.kind] || a.kind}</span>
            <span className="grow">{a.text}{a.outlet ? <span className="muted small"> · {a.outlet}</span> : null}</span>
            <span className="muted small nowrap">{new Date(a.at).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}</span>
          </li>
        ))}
      </ul>
      {data.unseen > 0 && <button className="link" onClick={async () => { await api("/alerts/seen", { method: "POST" }); setData({ ...data, unseen: 0, alerts: data.alerts.map((a) => ({ ...a, seen: true })) }); }}>Mark all as seen</button>}
    </Card>
  );
}
