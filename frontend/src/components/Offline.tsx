import { useEffect, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { state, subscribe, syncNow, type OfflineResult } from "../offline";
import { Receipt } from "./Receipt";

let snap = state();
const sub = (fn: () => void) => subscribe(() => { snap = state(); fn(); });
export const useOffline = () => useSyncExternalStore(sub, () => snap);

/** Shown on the billing screen while the box can't be reached, and while offline bills are uploading. */
export function OfflineBar() {
  const s = useOffline();
  if (s.online && !s.queued && !s.notes.length) return null;
  if (!s.online) {
    return (
      <div className="offline-bar off" role="status">
        <strong>Box not reachable. Billing on this counter only.</strong>
        <span>Cash, UPI and card bills still work and print here with their kitchen slips (series {s.series}).
          {s.queued > 0 && ` ${s.queued} bill${s.queued === 1 ? "" : "s"} waiting to upload.`} Keep this page open.</span>
      </div>
    );
  }
  return (
    <div className={`offline-bar ${s.failed.length ? "warn" : "sync"}`} role="status">
      {s.queued > 0 && !s.failed.length && <strong>{s.syncing ? "Uploading" : "Waiting to upload"} {s.queued} offline bill{s.queued === 1 ? "" : "s"}…</strong>}
      {s.failed.length > 0 && <>
        <strong>{s.failed.length} offline bill{s.failed.length === 1 ? "" : "s"} couldn't be added</strong>
        <ul>{s.failed.map((b) => <li key={b.id}>{b.invoice_no}: {b.error}</li>)}</ul>
        <button className="link" onClick={() => void syncNow()}>Try again</button>
      </>}
      {s.queued === 0 && s.notes.length > 0 && <>
        <strong>Offline bills uploaded. Please check:</strong>
        <ul>{s.notes.map((n) => <li key={n}>{n}</li>)}</ul>
      </>}
    </div>
  );
}

/** Prints the bill and one slip per kitchen area in a single print, for when the box can't print KOTs. */
export function PrintOffline({ result, onDone }: { result: OfflineResult; onDone: () => void }) {
  const [at] = useState(() => new Date(result.bill.created_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }));
  useEffect(() => {
    const t = setTimeout(() => { window.print(); onDone(); }, 350);
    return () => clearTimeout(t);
  }, [result, onDone]);
  const b = result.bill;
  return createPortal(
    <div id="print-root">
      <Receipt bill={b} />
      {result.kots.map((k) => (
        <div key={k.area} className="kot-slip kot-offline">
          <div className="ks-area">{k.area}</div>
          <div className="ks-token">Token #{b.token_no}</div>
          <div className="ks-mark">OFFLINE · not on the kitchen screen</div>
          <div className="ks-small">{b.order_type === "dine_in" ? `Table ${b.table_label}` : b.car_number ? `Car ${b.car_number}` : b.order_type === "delivery" ? "Delivery" : "Takeaway"} · {at}</div>
          <hr />
          {k.lines.map((l, i) => <div key={i} className="ks-line"><strong>{l.qty} × {l.name}</strong>{l.extra && <span className="ks-small">+ {l.extra}</span>}</div>)}
        </div>
      ))}
    </div>,
    document.body,
  );
}
