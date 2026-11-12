import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { formatPaise } from "../money";
import { QrImage } from "../qr";
import { PrintQrSlip, type QrSlipData } from "./Receipt";
import type { Bill } from "../types";
import { Button } from "../ui";

interface UpiReq { id: string; status: "waiting" | "paid" | "billed" | "closed" | "problem"; amount_paise: number; upi_string: string | null; provider: string;
  expires_at: string; payment_ref: string | null; payer: string | null; problem: string | null; order_id: string; bill: Bill | null;
  slip?: QrSlipData | null; print_qr?: boolean }

/** Shows the bill's own UPI QR and waits: the bill is made by itself when the money arrives. */
export default function UpiAuto({ request, onBilled, onClose, onLater }: { request: UpiReq; onBilled: (b: Bill) => void; onClose: (orderId: string) => void; onLater?: () => void }) {
  const [r, setR] = useState(request);
  // The QR on the bill: printed straight away when the restaurant asked for that, or on a tap.
  const [printing, setPrinting] = useState(() => !!(request.print_qr && request.slip && request.upi_string));
  const [printed, setPrinted] = useState(false);
  const secsLeft = (iso: string) => Math.max(0, Math.round((new Date(iso).getTime() - Date.now()) / 1000));
  const [left, setLeft] = useState(() => secsLeft(request.expires_at));
  const done = useRef(false);
  useEffect(() => {
    const t = setInterval(async () => {
      if (done.current) return;
      try {
        const x = await api<UpiReq>(`/upi/requests/${request.id}`);
        setR(x);
        if (x.status === "billed" && x.bill && !done.current) { done.current = true; setTimeout(() => onBilled(x.bill!), 900); }
      } catch { /* keep trying */ }
    }, 2000);
    return () => clearInterval(t);
  }, [request.id, onBilled]);
  useEffect(() => { const t = setInterval(() => setLeft(secsLeft(r.expires_at)), 1000); return () => clearInterval(t); }, [r.expires_at]);

  const cancel = async () => { const x = await api<UpiReq>(`/upi/requests/${r.id}/cancel`, { method: "POST" }); setR(x); if (x.status === "billed" && x.bill) { done.current = true; onBilled(x.bill); } else onClose(r.order_id); };
  return (
    <div className="modal-back">
      <div className="modal upi-auto" role="dialog" aria-label="UPI payment">
        {r.status === "waiting" && <>
          <h2>Scan to pay {formatPaise(r.amount_paise)}</h2>
          {r.upi_string && <QrImage text={r.upi_string} size={240} label="UPI QR for this bill" />}
          <p className="upi-wait"><span className="dot-pulse" aria-hidden /> Waiting for the payment. The bill prints by itself.</p>
          {printed && <p className="ok small">QR printed on the bill. You can take the next order: you'll see a message when this one is paid.</p>}
          <p className="muted small">QR closes in {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}. Any UPI app works.</p>
          <div className="row">
            {r.provider === "test" && <Button onClick={async () => setR(await api<UpiReq>(`/upi/requests/${r.id}/test-paid`, { method: "POST" }))}>Customer paid (test)</Button>}
            {r.slip && r.upi_string && <Button variant="ghost" onClick={() => setPrinting(true)}>{printed ? "Print again" : "Print QR on bill"}</Button>}
            {onLater && <Button variant="ghost" onClick={onLater}>Next order (keep this QR)</Button>}
            <Button variant="ghost" onClick={cancel}>Cancel, bill later</Button>
          </div>
          {r.provider === "test" && <p className="muted small">Test mode: no real money. Add your Paytm or Razorpay keys under Payments to go live.</p>}
          {r.problem && <p className="error small">{r.problem}. The customer can try again with the same QR.</p>}
        </>}
        {r.status === "billed" && <><h2 className="ok">Paid ✓</h2><p>{formatPaise(r.amount_paise)} received{r.payer ? ` from ${r.payer}` : ""}. Printing the bill…</p></>}
        {(r.status === "problem" || r.status === "closed") && <>
          <h2>{r.status === "problem" ? "Check this payment" : "QR closed"}</h2>
          <p className={r.status === "problem" ? "error" : "muted"}>{r.problem || "No payment came in."}</p>
          {r.payment_ref && <p className="small">UPI reference: <strong>{r.payment_ref}</strong>. Bill it from Open orders with UPI and this reference.</p>}
          <Button onClick={() => onClose(r.order_id)}>OK</Button>
        </>}
      </div>
      {printing && r.slip && r.upi_string && <PrintQrSlip slip={r.slip} upi={r.upi_string} expiresAt={r.expires_at} onDone={() => { setPrinting(false); setPrinted(true); }} />}
    </div>
  );
}
export type { UpiReq };

/** QRs still out (printed on a bill, or a dialog the cashier moved on from). Pops up when one is paid,
 *  like the Paytm notification, and offers to print the tax invoice. */
export function UpiWatcher({ outletId, hideId, onPrint }: { outletId: string; hideId?: string; onPrint: (b: Bill) => void }) {
  const [list, setList] = useState<UpiReq[]>([]);
  const [news, setNews] = useState<UpiReq[]>([]);
  const [open, setOpen] = useState(false);
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    let live = true;
    const tick = async () => {
      try {
        const xs = await api<UpiReq[]>(`/outlets/${outletId}/upi/waiting`);
        if (!live) return;
        const settled = xs.filter((x) => x.status === "billed" || x.status === "problem");
        if (seen.current === null) seen.current = new Set(settled.map((x) => x.id));   // don't pop up old ones on first load
        const fresh = settled.filter((x) => !seen.current!.has(x.id));
        fresh.forEach((x) => seen.current!.add(x.id));
        if (fresh.length) setNews((n) => [...n, ...fresh.filter((x) => x.id !== hideId)]);
        setList(xs.filter((x) => x.status === "waiting"));
      } catch { /* box busy: try again */ }
    };
    tick();
    const t = setInterval(tick, 4000);
    return () => { live = false; clearInterval(t); };
  }, [outletId, hideId]);
  const waiting = list.filter((x) => x.id !== hideId);
  const drop = (id: string) => setNews((n) => n.filter((x) => x.id !== id));
  const token = (x: UpiReq) => (x.slip?.token_no ? `Token #${x.slip.token_no}` : "A bill") + (x.slip?.table_label ? `, table ${x.slip.table_label}` : "");
  return (
    <div className="upi-watch" aria-live="polite">
      {news.map((x) => (
        <div key={x.id} className={`upi-toast ${x.status === "billed" ? "ok" : "warn"}`} role="status">
          {x.status === "billed" ? <>
            <strong>{token(x)}: {formatPaise(x.amount_paise)} paid by UPI</strong>
            <span>Bill {x.bill?.invoice_no} made{x.payment_ref ? `, ref ${x.payment_ref}` : ""}.</span>
            <div className="row">{x.bill && <Button onClick={() => { onPrint(x.bill!); drop(x.id); }}>Print bill</Button>}<Button variant="ghost" onClick={() => drop(x.id)}>OK</Button></div>
          </> : <>
            <strong>{token(x)}: check this UPI payment</strong>
            <span>{x.problem}{x.payment_ref ? ` (ref ${x.payment_ref})` : ""}</span>
            <div className="row"><Button variant="ghost" onClick={() => drop(x.id)}>OK</Button></div>
          </>}
        </div>
      ))}
      {waiting.length > 0 && <>
        <button className="upi-chip" onClick={() => setOpen(!open)}>{waiting.length} UPI {waiting.length === 1 ? "QR" : "QRs"} waiting</button>
        {open && <div className="upi-list">
          {waiting.map((x) => <div key={x.id} className="r-row"><span>{token(x)}</span><span>{formatPaise(x.amount_paise)}</span>
            <span className="row">
              {x.provider === "test" && <button className="link" onClick={() => api(`/upi/requests/${x.id}/test-paid`, { method: "POST" })}>Paid (test)</button>}
              <button className="link" onClick={async () => { await api(`/upi/requests/${x.id}/cancel`, { method: "POST" }); setList((l) => l.filter((y) => y.id !== x.id)); }}>Stop</button>
            </span></div>)}
        </div>}
      </>}
    </div>
  );
}
