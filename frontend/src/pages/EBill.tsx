import { useEffect, useState } from "react";
import { api } from "../api";
import { Receipt } from "../components/Receipt";
import type { Bill } from "../types";

/** The customer's e-bill, opened from the SMS link. No login. */
export default function EBill({ shareKey }: { shareKey: string }) {
  const [bill, setBill] = useState<Bill | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    api<Bill>(`/public/bills/${encodeURIComponent(shareKey)}`).then((b) => { setBill(b); document.title = `Bill ${b.invoice_no}`; })
      .catch(() => setMissing(true));
  }, [shareKey]);
  if (missing) return <div className="splash">This bill link isn't valid. Ask the restaurant to send it again.</div>;
  if (!bill) return <div className="splash">Opening your bill…</div>;
  return (
    <div className="ebill">
      <div className="ebill-paper"><Receipt bill={bill} /></div>
      <button className="btn btn-ghost ebill-print" onClick={() => window.print()}>Save as PDF / print</button>
    </div>
  );
}
