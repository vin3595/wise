import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { formatPaise } from "../money";
import type { Bill } from "../types";
import { Button } from "../ui";

export interface CardReq { id: string; status: "waiting" | "billed" | "failed" | "closed" | "problem"; amount_paise: number; provider: string; terminal: string | null;
  expires_at: string; payment_ref: string | null; problem: string | null; order_id: string; bill: Bill | null }

/** The amount is on the card machine; waits for the customer's card, then the bill prints by itself. */
export default function CardAuto({ request, onBilled, onClose, onRetry }: { request: CardReq; onBilled: (b: Bill) => void; onClose: () => void; onRetry: () => void }) {
  const [r, setR] = useState(request);
  const done = useRef(false);
  useEffect(() => {
    const t = setInterval(async () => {
      if (done.current) return;
      try {
        const x = await api<CardReq>(`/card/requests/${request.id}`);
        setR(x);
        if (x.status === "billed" && x.bill && !done.current) { done.current = true; setTimeout(() => onBilled(x.bill!), 900); }
      } catch { /* keep trying */ }
    }, 1500);
    return () => clearInterval(t);
  }, [request.id, onBilled]);
  const test = async (approved: boolean) => {
    const x = await api<CardReq>(`/card/requests/${r.id}/test-result?approved=${approved}`, { method: "POST" });
    setR(x);
    if (x.status === "billed" && x.bill) { done.current = true; setTimeout(() => onBilled(x.bill!), 900); }
  };
  const cancel = async () => { const x = await api<CardReq>(`/card/requests/${r.id}/cancel`, { method: "POST" }); setR(x); if (x.status === "billed" && x.bill) { done.current = true; onBilled(x.bill); } else onClose(); };
  return (
    <div className="modal-back">
      <div className="modal upi-auto" role="dialog" aria-label="Card payment">
        {r.status === "waiting" && <>
          <h2>{formatPaise(r.amount_paise)} sent to {r.terminal}</h2>
          <div className="card-art" aria-hidden><span /></div>
          <p className="upi-wait"><span className="dot-pulse" aria-hidden /> Ask the customer to tap, insert or swipe their card.</p>
          <p className="muted small">The bill prints by itself once the card is approved. Nothing to type on the machine.</p>
          <div className="row">
            {r.provider === "test" && <>
              <Button onClick={() => test(true)}>Card approved (test)</Button>
              <Button variant="ghost" onClick={() => test(false)}>Declined (test)</Button>
            </>}
            <Button variant="ghost" onClick={cancel}>Cancel, bill later</Button>
          </div>
        </>}
        {r.status === "billed" && <><h2 className="ok">Approved ✓</h2><p>{formatPaise(r.amount_paise)}{r.payment_ref ? ` · ${r.payment_ref}` : ""}. Printing the bill…</p></>}
        {(r.status === "failed" || r.status === "closed" || r.status === "problem") && <>
          <h2>{r.status === "failed" ? "Card not approved" : r.status === "problem" ? "Check this payment" : "Cancelled"}</h2>
          <p className={r.status === "closed" ? "muted" : "error"}>{r.problem || "The machine stopped waiting."}</p>
          {r.payment_ref && <p className="small">Card reference: <strong>{r.payment_ref}</strong></p>}
          <div className="row">
            {r.status === "failed" && <Button onClick={onRetry}>Try the card again</Button>}
            <Button variant="ghost" onClick={onClose}>OK, bill later</Button>
          </div>
        </>}
      </div>
    </div>
  );
}
