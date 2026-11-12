import { useEffect, useState } from "react";
import { api } from "../api";
import { bpToPercent } from "../money";
import type { TaxGroup } from "../types";
import { Button, Card, ErrorText, Field, Input, Toggle, useAction } from "../ui";

const pctToBp = (s: string) => Math.round(Number(s || "0") * 100);

export default function TaxGroups() {
  const [groups, setGroups] = useState<TaxGroup[]>([]);
  const [editing, setEditing] = useState<TaxGroup | "new" | null>(null);
  const load = () => api<TaxGroup[]>("/menu/tax-groups").then(setGroups);
  useEffect(() => { load(); }, []);

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Tax groups</h1>
        {!editing && <Button onClick={() => setEditing("new")}>New tax group</Button>}
      </div>
      <p className="muted">Most standalone restaurants charge 5% GST (2.5% CGST + 2.5% SGST) without input tax credit. Confirm your rate with your CA.</p>
      {editing && <TaxForm group={editing === "new" ? null : editing} onDone={() => { setEditing(null); load(); }} />}
      <Card>
        <table className="table">
          <thead><tr><th>Name</th><th>CGST</th><th>SGST</th><th>IGST</th><th>Prices</th><th /></tr></thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.id} className={g.is_active ? "" : "inactive"}>
                <td>{g.name} {g.is_default && <span className="badge">default</span>}</td>
                <td>{bpToPercent(g.cgst_bp)}%</td><td>{bpToPercent(g.sgst_bp)}%</td><td>{bpToPercent(g.igst_bp)}%</td>
                <td>{g.is_inclusive ? "Include tax" : "Tax added on top"}</td>
                <td><button className="link" onClick={() => setEditing(g)}>Edit</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function TaxForm({ group, onDone }: { group: TaxGroup | null; onDone: () => void }) {
  const [f, setF] = useState({
    name: group?.name || "", cgst: bpToPercent(group?.cgst_bp ?? 250), sgst: bpToPercent(group?.sgst_bp ?? 250),
    igst: bpToPercent(group?.igst_bp ?? 0), is_inclusive: group?.is_inclusive ?? false, is_default: group?.is_default ?? false,
    is_active: group?.is_active ?? true,
  });
  const { busy, error, run } = useAction();
  return (
    <Card title={group ? `Edit ${group.name}` : "New tax group"}>
      <form onSubmit={(e) => {
        e.preventDefault();
        run(async () => {
          const body = { name: f.name, cgst_bp: pctToBp(f.cgst), sgst_bp: pctToBp(f.sgst), igst_bp: pctToBp(f.igst),
            is_inclusive: f.is_inclusive, is_default: f.is_default, is_active: f.is_active };
          await api(group ? `/menu/tax-groups/${group.id}` : "/menu/tax-groups", { method: group ? "PATCH" : "POST", body });
          onDone();
        });
      }}>
        <Field label="Name"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></Field>
        <div className="grid3">
          <Field label="CGST %"><Input inputMode="decimal" value={f.cgst} onChange={(e) => setF({ ...f, cgst: e.target.value })} /></Field>
          <Field label="SGST %"><Input inputMode="decimal" value={f.sgst} onChange={(e) => setF({ ...f, sgst: e.target.value })} /></Field>
          <Field label="IGST %" hint="Only for inter-state supply"><Input inputMode="decimal" value={f.igst} onChange={(e) => setF({ ...f, igst: e.target.value })} /></Field>
        </div>
        <Toggle checked={f.is_inclusive} onChange={(v) => setF({ ...f, is_inclusive: v })} label="Menu prices already include tax" />
        <Toggle checked={f.is_default} onChange={(v) => setF({ ...f, is_default: v })} label="Default for new items" />
        <Toggle checked={f.is_active} onChange={(v) => setF({ ...f, is_active: v })} label="Active" />
        <ErrorText error={error} />
        <div className="row">
          <Button disabled={busy}>Save</Button>
          <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>
        </div>
      </form>
    </Card>
  );
}
