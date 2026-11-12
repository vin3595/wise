import { useEffect, useState } from "react";
import { api } from "../api";
import type { Brand, Outlet } from "../types";
import { Button, Card, ErrorText, Field, Input, Select, Toggle, useAction } from "../ui";

type Form = Omit<Outlet, "id" | "display_key" | "qr_key">;

const empty = (brandId: string): Form => ({
  brand_id: brandId, name: "", code: "", gstin: null, fssai: null, address: null, state_code: "08", phone: null,
  bill_footer: null, car_number_required: false, stock_check_frequency_days: 7, is_active: true,
  upi_vpa: null, upi_payee_name: null, qr_ordering: false, qr_auto_accept: false, kiosk_enabled: false, print_kots: false, table_labels: [],
});

/** "T1-T12, Garden 1" -> ["T1".."T12", "GARDEN 1"] */
function parseTables(text: string): string[] {
  const out: string[] = [];
  for (const part of text.split(",").map((p) => p.trim()).filter(Boolean)) {
    const m = part.match(/^([A-Za-z]*)(\d+)\s*-\s*\1?(\d+)$/);
    if (m && Number(m[3]) >= Number(m[2]) && Number(m[3]) - Number(m[2]) < 200) {
      for (let n = Number(m[2]); n <= Number(m[3]); n++) out.push(`${m[1]}${n}`.toUpperCase());
    } else out.push(part.toUpperCase());
  }
  return [...new Set(out)];
}

export default function Outlets() {
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [brands, setBrands] = useState<Brand[]>([]);
  const [editing, setEditing] = useState<{ id: string | null; form: Form } | null>(null);
  const { busy, error, run } = useAction();

  const load = async () => {
    const [o, b] = await Promise.all([api<Outlet[]>("/tenant/outlets"), api<Brand[]>("/tenant/brands")]);
    setOutlets(o);
    setBrands(b);
  };
  useEffect(() => { load(); }, []);

  const brandName = (id: string) => brands.find((b) => b.id === id)?.name || "";
  const f = editing?.form;
  const set = <K extends keyof Form>(k: K, v: Form[K]) => editing && setEditing({ ...editing, form: { ...editing.form, [k]: v } });
  const blank = (v: string) => (v.trim() ? v : null);

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Outlets</h1>
        {!editing && brands.length > 0 && <Button onClick={() => setEditing({ id: null, form: empty(brands[0].id) })}>Add outlet</Button>}
      </div>
      {editing && f && (
        <Card title={editing.id ? "Edit outlet" : "New outlet"}>
          <form onSubmit={(e) => {
            e.preventDefault();
            run(async () => {
              await api(editing.id ? `/tenant/outlets/${editing.id}` : "/tenant/outlets", { method: editing.id ? "PATCH" : "POST", body: f });
              setEditing(null);
              await load();
            });
          }}>
            <div className="grid2">
              <Field label="Outlet name"><Input value={f.name} onChange={(e) => set("name", e.target.value)} required /></Field>
              <Field label="Brand"><Select value={f.brand_id} onChange={(e) => set("brand_id", e.target.value)}>
                {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </Select></Field>
              <Field label="Short code" hint="2-4 letters/numbers, starts every bill number (PT1/2627/000123)">
                <Input value={f.code} maxLength={4} onChange={(e) => set("code", e.target.value.toUpperCase())} required />
              </Field>
              <Field label="GSTIN"><Input value={f.gstin || ""} onChange={(e) => set("gstin", blank(e.target.value.toUpperCase()))} /></Field>
              <Field label="FSSAI licence no."><Input value={f.fssai || ""} onChange={(e) => set("fssai", blank(e.target.value))} /></Field>
              <Field label="State code" hint="Rajasthan = 08"><Input value={f.state_code || ""} maxLength={2} onChange={(e) => set("state_code", blank(e.target.value))} /></Field>
              <Field label="Phone"><Input value={f.phone || ""} onChange={(e) => set("phone", blank(e.target.value))} /></Field>
              <Field label="Stock check every (days)"><Input type="number" min={1} max={90} value={f.stock_check_frequency_days}
                onChange={(e) => set("stock_check_frequency_days", Number(e.target.value))} /></Field>
            </div>
            <Field label="Address"><Input value={f.address || ""} onChange={(e) => set("address", blank(e.target.value))} /></Field>
            <div className="grid2">
              <Field label="UPI ID for bill QR" hint="e.g. shakewallah@okaxis"><Input value={f.upi_vpa || ""} onChange={(e) => set("upi_vpa", blank(e.target.value.trim()))} /></Field>
              <Field label="UPI payee name"><Input value={f.upi_payee_name || ""} onChange={(e) => set("upi_payee_name", blank(e.target.value))} /></Field>
            </div>
            <Field label="Tables" hint="Comma-separated; ranges allowed, e.g. T1-T12, Garden 1">
              <TablesInput value={f.table_labels || []} onChange={(v) => set("table_labels", v)} />
            </Field>
            <Field label="Bill footer"><Input value={f.bill_footer || ""} onChange={(e) => set("bill_footer", blank(e.target.value))} /></Field>
            <Toggle checked={f.car_number_required} onChange={(v) => set("car_number_required", v)} label="Car number required on takeaway orders" />
            <Toggle checked={f.qr_ordering} onChange={(v) => set("qr_ordering", v)} label="Customers can order by scanning the table QR" />
            {f.qr_ordering && <Toggle checked={f.qr_auto_accept} onChange={(v) => set("qr_auto_accept", v)} label="Send QR orders to the kitchen without staff accepting" />}
            <Toggle checked={f.kiosk_enabled} onChange={(v) => set("kiosk_enabled", v)} label="Self-order kiosk (touch screen)" />
            <Toggle checked={!!f.print_kots} onChange={(v) => set("print_kots", v)} label="Print KOTs (set printers under Kitchen setup)" />
            <Toggle checked={f.is_active} onChange={(v) => set("is_active", v)} label="Outlet active" />
            <ErrorText error={error} />
            <div className="row">
              <Button disabled={busy}>Save</Button>
              <Button type="button" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
            </div>
          </form>
        </Card>
      )}
      <Card>
        {outlets.some((o) => o.is_training && o.is_active) && <p className="muted small">Training copies are for practice: staff pick them at sign-in. Bills there print "TRAINING", use no real UPI or card, give no loyalty, send no SMS and never reach reports, GST or Tally.</p>}
        {outlets.length === 0 ? <p className="muted">No outlets yet.</p> : (
          <table className="table">
            <thead><tr><th>Outlet</th><th>Brand</th><th>Code</th><th>GSTIN</th><th /></tr></thead>
            <tbody>
              {outlets.filter((o) => o.is_active || !o.is_training).map((o) => (
                <tr key={o.id} className={o.is_active ? "" : "inactive"}>
                  <td>{o.name}{o.is_training && <span className="badge training-badge">training</span>}</td><td>{brandName(o.brand_id)}</td><td>{o.code}</td><td>{o.gstin || "—"}</td>
                  <td className="row">
                    {!o.is_training && <button className="link" onClick={() => { const { id, display_key: _d, qr_key: _q, print_key: _p, kitchen_key: _k, is_training: _t, training_of: _f, ...form } = o; void _d; void _q; void _p; void _k; void _t; void _f; setEditing({ id, form: { ...form, table_labels: form.table_labels || [] } }); }}>Edit</button>}
                    {!o.is_training && o.is_active && !outlets.some((t) => t.training_of === o.id && t.is_active) && (
                      <button className="link" onClick={() => run(async () => { await api(`/tenant/outlets/${o.id}/training`, { method: "POST" }); await load(); })}>Make a training copy</button>
                    )}
                    {o.is_training && <button className="link" onClick={() => {
                      if (confirm("Start practice again from clean? The old practice stays out of every report.")) run(async () => { await api(`/tenant/outlets/${o.id}/training/reset`, { method: "POST" }); await load(); });
                    }}>Start fresh</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function TablesInput({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState(value.join(", "));
  return <Input value={text} onChange={(e) => setText(e.target.value)} onBlur={() => { const t = parseTables(text); onChange(t); setText(t.join(", ")); }} placeholder="T1-T12" />;
}
