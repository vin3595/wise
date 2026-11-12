import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, download } from "../api";
import type { Brand, ImportResult } from "../types";
import { Button, Card, ErrorText, Field, Select, useAction } from "../ui";

export default function MenuImport() {
  const [brands, setBrands] = useState<Brand[]>([]);
  const [brandId, setBrandId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const { busy, error, run } = useAction();

  useEffect(() => {
    api<Brand[]>("/tenant/brands").then((b) => { setBrands(b); if (b[0]) setBrandId(b[0].id); });
  }, []);

  function send(dryRun: boolean) {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    run(async () => setResult(await api<ImportResult>(`/menu/import?brand_id=${brandId}&dry_run=${dryRun}`, { form: fd })));
  }

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Import menu from Excel</h1>
        <Link to="/menu" className="btn btn-ghost">Back to menu</Link>
      </div>
      <Card title="1. Get the template">
        <p className="muted">One row per variant. Rows with the same item name become variants (300 ML, 1000 ML). An optional "Addons" sheet adds add-on groups.</p>
        <Button variant="ghost" onClick={() => run(() => download("/menu/import/template", "wise-menu-template.xlsx"))}>Download template</Button>
      </Card>
      <Card title="2. Upload and check">
        {brands.length > 1 && (
          <Field label="Brand"><Select value={brandId} onChange={(e) => { setBrandId(e.target.value); setResult(null); }}>
            {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </Select></Field>
        )}
        <input type="file" accept=".xlsx" onChange={(e) => { setFile(e.target.files?.[0] || null); setResult(null); }} />
        <div className="row">
          <Button disabled={!file || busy} onClick={() => send(true)}>Check file</Button>
          {result?.dry_run && result.ok && <Button disabled={busy} onClick={() => send(false)}>Import now</Button>}
        </div>
        <ErrorText error={error} />
        <p className="muted small">Importing updates items with the same name and adds new ones. Nothing is deleted.</p>
      </Card>
      {result && (
        <Card title={result.dry_run ? (result.ok ? "Ready to import" : "Fix these rows first") : "Imported"}>
          <p>
            {result.summary.categories} categories · {result.summary.items} items · {result.summary.variants} variants ·{" "}
            {result.summary.addon_groups} add-on groups
          </p>
          {result.created && (
            <p className="ok">
              Added {result.created.created.items} items and {result.created.created.variants} variants; updated{" "}
              {result.created.updated.items} items.
            </p>
          )}
          {result.errors.length > 0 && <IssueTable title="Errors" rows={result.errors} />}
          {result.warnings.length > 0 && <IssueTable title="Warnings" rows={result.warnings} />}
        </Card>
      )}
    </div>
  );
}

function IssueTable({ title, rows }: { title: string; rows: ImportResult["errors"] }) {
  return (
    <>
      <h3>{title}</h3>
      <table className="table">
        <thead><tr><th>Sheet</th><th>Row</th><th>Problem</th></tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}><td>{r.sheet}</td><td>{r.row || "—"}</td><td>{r.message}</td></tr>)}</tbody>
      </table>
    </>
  );
}
