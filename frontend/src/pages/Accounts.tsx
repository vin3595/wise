import { useEffect, useState } from "react";
import { api, download } from "../api";
import { useSession } from "../session";
import type { Outlet } from "../types";
import { Button, Card, ErrorText, Field, Input, Select, useAction } from "../ui";

interface Acc { company_name: string; ledgers: Record<string, string>; defaults: Record<string, string> }
const LABELS: [string, string][] = [
  ["cash", "Cash received"], ["upi", "UPI received"], ["card", "Card received"], ["due", "Credit (due) bills"], ["complimentary", "Complimentary bills"],
  ["sales", "Sales (one per GST rate, {rate} = 5, 12…)"], ["cgst", "Output CGST"], ["sgst", "Output SGST"], ["igst", "Output IGST"], ["round_off", "Round off"],
  ["purchase", "Purchases (per GST rate)"], ["in_cgst", "Input CGST"], ["in_sgst", "Input SGST"],
];
const iso = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
function month(offset: number): [string, string] {
  const n = new Date(), a = new Date(n.getFullYear(), n.getMonth() + offset, 1), b = new Date(n.getFullYear(), n.getMonth() + offset + 1, 0);
  return [iso(a), iso(offset === 0 ? n : b)];
}

/** Files for the accountant: Tally import and Excel registers. */
export default function Accounts() {
  const { can } = useSession();
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [outletId, setOutletId] = useState("");
  const [[d1, d2], setRange] = useState<[string, string]>(month(-1));
  const [acc, setAcc] = useState<Acc | null>(null);
  const [edit, setEdit] = useState(false);
  const [saved, setSaved] = useState(false);
  const { busy, error, run } = useAction();
  useEffect(() => { api<Outlet[]>("/tenant/outlets").then(setOutlets); api<Acc>("/tenant/accounts").then(setAcc); }, []);
  const q = `date_from=${d1}&date_to=${d2}${outletId ? `&outlet_id=${outletId}` : ""}`;
  const get = (path: string, name: string) => run(() => download(path, name));
  return (
    <div className="stack">
      <h1>Accounts</h1>
      <Card title="Period">
        <div className="row">
          <div className="seg three" style={{ marginBottom: 0 }}>
            <button className={d1 === month(-1)[0] ? "active" : ""} onClick={() => setRange(month(-1))}>Last month</button>
            <button className={d1 === month(0)[0] ? "active" : ""} onClick={() => setRange(month(0))}>This month</button>
            <button onClick={() => { const t = iso(new Date()); setRange([t, t]); }} className={d1 === d2 ? "active" : ""}>Today</button>
          </div>
          <Input type="date" value={d1} onChange={(e) => setRange([e.target.value, d2])} aria-label="From" />
          <Input type="date" value={d2} onChange={(e) => setRange([d1, e.target.value])} aria-label="To" />
          {outlets.length > 1 && <Select value={outletId} onChange={(e) => setOutletId(e.target.value)} aria-label="Outlet"><option value="">All outlets</option>{outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</Select>}
        </div>
      </Card>
      <div className="grid2 wide">
        <Card title="Excel for the accountant">
          <p className="muted">One workbook: every bill (sales register), GST by rate, HSN summary, day-wise money by payment mode, and the purchase register. Ready for GSTR-1 and GSTR-3B.</p>
          <Button disabled={busy} onClick={() => get(`/accounts/excel?${q}`, `wise-accounts-${d1}-to-${d2}.xlsx`)}>Download Excel</Button>
        </Card>
        <Card title="Tally Prime">
          <ol className="steps small">
            <li><strong>Once:</strong> download the ledgers file and import it in Tally (Gateway of Tally &gt; Import &gt; Masters). It creates Cash, UPI, Sales @5%, Output CGST and the rest.</li>
            <li><strong>Every month:</strong> download sales and purchases, then Import &gt; Transactions. Sales come in as one voucher per outlet per day; purchases one per supplier bill.</li>
          </ol>
          <div className="row">
            <Button variant="ghost" disabled={busy} onClick={() => get(`/accounts/tally?kind=masters&${q}`, "wise-tally-ledgers.xml")}>Ledgers (once)</Button>
            <Button disabled={busy} onClick={() => get(`/accounts/tally?kind=sales&${q}`, `wise-tally-sales-${d1}-to-${d2}.xml`)}>Sales vouchers</Button>
            <Button disabled={busy} onClick={() => get(`/accounts/tally?kind=purchases&${q}`, `wise-tally-purchases-${d1}-to-${d2}.xml`)}>Purchase vouchers</Button>
          </div>
          <p className="muted small">B2B bills (with the customer's GSTIN) are inside the day's sales voucher; their details for GSTR-1 are in the Excel and in Reports &gt; B2B bills.</p>
        </Card>
      </div>
      <ErrorText error={error} />
      {acc && (
        <Card title="Tally names" actions={can("tenant.settings") && !edit ? <Button variant="ghost" onClick={() => setEdit(true)}>Change names</Button> : undefined}>
          <p className="muted small">The files use these company and ledger names. Match them to what your accountant already has in Tally, so nothing is created twice.</p>
          {!edit ? (
            <table className="table"><tbody>
              <tr><td>Company in Tally</td><td><strong>{acc.company_name}</strong></td></tr>
              {LABELS.map(([k, l]) => <tr key={k}><td>{l}</td><td>{acc.ledgers[k]}</td></tr>)}
            </tbody></table>
          ) : (
            <>
              <Field label="Company in Tally (exact name)"><Input value={acc.company_name} onChange={(e) => setAcc({ ...acc, company_name: e.target.value })} /></Field>
              <div className="grid2">{LABELS.map(([k, l]) => <Field key={k} label={l}><Input value={acc.ledgers[k]} onChange={(e) => setAcc({ ...acc, ledgers: { ...acc.ledgers, [k]: e.target.value } })} /></Field>)}</div>
              <div className="row">
                <Button disabled={busy} onClick={() => run(async () => { setAcc(await api<Acc>("/tenant/accounts", { method: "PUT", body: { company_name: acc.company_name, ledgers: acc.ledgers } })); setEdit(false); setSaved(true); })}>Save</Button>
                <Button variant="ghost" onClick={() => { setEdit(false); api<Acc>("/tenant/accounts").then(setAcc); }}>Cancel</Button>
              </div>
            </>
          )}
          {saved && <p className="ok">Saved.</p>}
        </Card>
      )}
    </div>
  );
}
