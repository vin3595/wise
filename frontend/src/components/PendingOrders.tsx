import { useRef, useState } from "react";
import { api } from "../api";
import { chime, usePolling } from "../hooks";
import { formatPaise } from "../money";
import { useSession } from "../session";
import type { Bill, Order } from "../types";
import { Button, ErrorText, Input } from "../ui";

/** QR and kiosk orders waiting for staff: accept/reject, or confirm the kiosk payment. */
export default function PendingOrders({ outletId, onBilled }: { outletId: string; onBilled: (b: Bill) => void }) {
  const { can } = useSession();
  const [orders, setOrders] = useState<Order[]>([]);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef<Set<string> | null>(null);

  usePolling(async () => {
    const list = await api<Order[]>(`/orders-pending?outlet_id=${outletId}`);
    if (seen.current && list.some((o) => !seen.current!.has(o.id))) chime();
    seen.current = new Set(list.map((o) => o.id));
    setOrders(list);
  }, 4000, [outletId], { outletId, topics: ["orders"] });

  async function act(o: Order, fn: () => Promise<unknown>) {
    setBusy(o.id);
    setError(null);
    try {
      const res = await fn();
      setOrders((l) => l.filter((x) => x.id !== o.id));
      // accept-paid returns a bill; plain accept returns the order (which also has an invoice_no field).
      if (res && typeof res === "object" && "payments" in res && "outlet" in res) onBilled(res as Bill);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(null);
    }
  }

  if (orders.length === 0) return null;
  return (
    <div className="pending">
      <h3>Waiting for you <span className="count">{orders.length}</span></h3>
      <ErrorText error={error} />
      {orders.map((o) => {
        const total = o.quoted_total_paise ?? 0;
        const kiosk = o.source === "kiosk";
        return (
          <div key={o.id} className="pending-card">
            <div className="row">
              <span className={`badge ${kiosk ? "kiosk" : "qr"}`}>{kiosk ? `Kiosk · ${o.pay_ref}` : "QR order"}</span>
              <strong>{o.table_label ? `Table ${o.table_label}` : o.order_type === "takeaway" ? (o.car_number ? `Takeaway · Car ${o.car_number}` : "Takeaway") : "Dine-in (counter pickup)"}</strong>
              <span className="muted small">{o.customer_name} · {o.customer_phone}</span>
            </div>
            <ul className="small">
              {o.pending_lines.map((l, i) => (
                <li key={i}>{l.qty}× {l.item}{l.variant !== "Regular" ? ` (${l.variant})` : ""}{l.addons.length ? ` + ${l.addons.join(", ")}` : ""}{l.notes ? ` · “${l.notes}”` : ""}</li>
              ))}
            </ul>
            {kiosk && <p className="small">{o.pay_method === "upi" ? "Paying by UPI" : "Will pay at the counter"}: <strong>{formatPaise(total)}</strong></p>}
            {rejecting === o.id ? (
              <div className="row">
                <Input placeholder="Reason shown to the customer, e.g. item finished" value={reason} onChange={(e) => setReason(e.target.value)} />
                <Button variant="danger" disabled={busy === o.id || reason.trim().length < 3}
                  onClick={() => act(o, () => api(`/orders/${o.id}/reject`, { body: { reason } })).then(() => { setRejecting(null); setReason(""); })}>Reject</Button>
                <button className="link" onClick={() => setRejecting(null)}>Back</button>
              </div>
            ) : (
              <div className="row">
                {kiosk ? (can("billing") ? <>
                  <Button disabled={busy === o.id} onClick={() => act(o, () => api<Bill>(`/orders/${o.id}/accept-paid`, { body: { mode: "upi" } }))}>UPI received {formatPaise(total)}</Button>
                  <Button variant="ghost" disabled={busy === o.id} onClick={() => act(o, () => api<Bill>(`/orders/${o.id}/accept-paid`, { body: { mode: "cash" } }))}>Paid cash</Button>
                  <Button variant="ghost" disabled={busy === o.id} onClick={() => act(o, () => api<Bill>(`/orders/${o.id}/accept-paid`, { body: { mode: "card" } }))}>Card</Button>
                </> : <span className="muted small">A cashier confirms the payment</span>) : (
                  <Button disabled={busy === o.id} onClick={() => act(o, () => api(`/orders/${o.id}/accept`, { method: "POST" }))}>Accept & send to kitchen</Button>
                )}
                <button className="link" onClick={() => setRejecting(o.id)}>Reject</button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
