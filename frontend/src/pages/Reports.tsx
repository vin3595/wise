import { useEffect, useMemo, useState } from "react";
import { api, tokenStore } from "../api";
import { formatPaise } from "../money";
import type { Outlet } from "../types";
import { ErrorText, Input, Select } from "../ui";

interface Summary {
  bills: number; cancelled_bills: number; discount_paise: number; cgst_paise: number; sgst_paise: number;
  igst_paise: number; sales_paise: number; avg_bill_paise: number; by_mode: Record<string, number>;
}
type Kind = "text" | "money" | "int" | "percent" | "date" | "time" | "datetime";
interface ReportTable {
  title: string; note: string | null; columns: { key: string; label: string; kind: Kind }[];
  rows: Record<string, string | number | null>[]; totals: Record<string, string | number> | null;
  date_from: string; date_to: string;
}
interface ReportInfo { name: string; group: string; label: string }

// Business day runs 4 am to 4 am, India time.
const bizToday = () => new Date(Date.now() + 5.5 * 3600e3 - 4 * 3600e3);
const iso = (d: Date) => d.toISOString().slice(0, 10);
const shift = (d: Date, days: number) => new Date(d.getTime() + days * 86400e3);
function preset(p: string): [string, string] {
  const t = bizToday();
  if (p === "yesterday") return [iso(shift(t, -1)), iso(shift(t, -1))];
  if (p === "7d") return [iso(shift(t, -6)), iso(t)];
  if (p === "month") return [iso(new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), 1))), iso(t)];
  if (p === "lastmonth") {
    const first = new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth() - 1, 1));
    return [iso(first), iso(new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), 0)))];
  }
  return [iso(t), iso(t)];
}
const PRESETS = [["today", "Today"], ["yesterday", "Yesterday"], ["7d", "Last 7 days"], ["month", "This month"], ["lastmonth", "Last month"]];
const MODE: Record<string, string> = { cash: "Cash", upi: "UPI", card: "Card", due: "Credit", complimentary: "Comp" };

function show(v: string | number | null | undefined, kind: Kind): string {
  if (v === null || v === undefined || v === "") return "";
  if (kind === "money") return formatPaise(Number(v));
  if (kind === "percent") return `${(Number(v) / 100).toFixed(1)}%`;
  if (kind === "int") return Number(v).toLocaleString("en-IN");
  if (kind === "date") return new Date(`${v}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  if (kind === "datetime") {
    const d = new Date(String(v));
    return isNaN(d.getTime()) ? String(v) : d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
  }
  return String(v);
}
const numeric = (k: Kind) => k === "money" || k === "int" || k === "percent";

export default function Reports() {
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [outletId, setOutletId] = useState("");
  const [range, setRange] = useState(preset("today"));
  const [picked, setPicked] = useState("today");
  const [list, setList] = useState<ReportInfo[]>([]);
  const [name, setName] = useState("orders");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [table, setTable] = useState<ReportTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [find, setFind] = useState("");
  const [sort, setSort] = useState<{ key: string; desc: boolean } | null>(null);

  useEffect(() => {
    api<Outlet[]>("/tenant/outlets").then(setOutlets).catch(() => {});
    api<ReportInfo[]>("/reports/list").then(setList).catch((e) => setError(e.message));
  }, []);
  const params = useMemo(() => {
    const p = new URLSearchParams({ date_from: range[0], date_to: range[1] });
    if (outletId) p.set("outlet_id", outletId);
    return p;
  }, [range, outletId]);
  useEffect(() => { api<Summary>(`/reports/summary?${params}`).then(setSummary).catch((e) => setError(e.message)); }, [params]);
  useEffect(() => {
    setLoading(true);
    setSort(null);
    api<ReportTable>(`/reports/r/${name}?${params}`).then((t) => { setTable(t); setError(null); })
      .catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, [name, params]);

  const rows = useMemo(() => {
    if (!table) return [];
    const q = find.trim().toLowerCase();
    let r = q ? table.rows.filter((row) => Object.values(row).some((v) => String(v ?? "").toLowerCase().includes(q))) : table.rows;
    if (sort) {
      const col = table.columns.find((c) => c.key === sort.key);
      r = [...r].sort((a, b) => {
        const x = a[sort.key], y = b[sort.key];
        const d = col && numeric(col.kind) ? Number(x ?? 0) - Number(y ?? 0) : String(x ?? "").localeCompare(String(y ?? ""));
        return sort.desc ? -d : d;
      });
    }
    return r;
  }, [table, find, sort]);

  async function download() {
    try {
      const res = await fetch(`/api/reports/r/${name}?${params}&format=csv`, { headers: { Authorization: `Bearer ${tokenStore.get()}` } });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(typeof body?.detail === "string" ? body.detail : `Download failed (${res.status})`);
      }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = (res.headers.get("content-disposition") || "").match(/filename="([^"]+)"/)?.[1] || `${name}.csv`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  const groups = list.reduce<Record<string, ReportInfo[]>>((g, r) => ({ ...g, [r.group]: [...(g[r.group] || []), r] }), {});
  const shown = rows.slice(0, 1000);

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Reports</h1>
        <div className="row">
          {outlets.length > 1 && (
            <Select value={outletId} onChange={(e) => setOutletId(e.target.value)} aria-label="Outlet">
              <option value="">All outlets</option>
              {outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </Select>
          )}
        </div>
      </div>
      <div className="range-bar">
        {PRESETS.map(([k, l]) => (
          <button key={k} className={`choice ${picked === k ? "on" : ""}`} onClick={() => { setPicked(k); setRange(preset(k)); }}>{l}</button>
        ))}
        <span className="range-custom">
          <Input type="date" value={range[0]} aria-label="From" onChange={(e) => { setPicked(""); setRange([e.target.value, range[1] < e.target.value ? e.target.value : range[1]]); }} />
          <span className="muted">to</span>
          <Input type="date" value={range[1]} aria-label="To" onChange={(e) => { setPicked(""); setRange([range[0] > e.target.value ? e.target.value : range[0], e.target.value]); }} />
        </span>
      </div>
      <ErrorText error={error} />
      {summary && (
        <div className="stats">
          <div className="stat"><span>Net sales</span><strong>{formatPaise(summary.sales_paise)}</strong></div>
          <div className="stat"><span>Bills</span><strong>{summary.bills}</strong></div>
          <div className="stat"><span>Average bill</span><strong>{formatPaise(summary.avg_bill_paise)}</strong></div>
          <div className="stat"><span>Discounts</span><strong>{formatPaise(summary.discount_paise)}</strong></div>
          <div className="stat"><span>GST collected</span><strong>{formatPaise(summary.cgst_paise + summary.sgst_paise + summary.igst_paise)}</strong></div>
          <div className="stat"><span>Cancelled bills</span><strong>{summary.cancelled_bills}</strong></div>
          <div className="stat stat-wide"><span>Paid by</span>
            <div className="mode-split">{Object.entries(summary.by_mode).sort((a, b) => b[1] - a[1]).map(([m, v]) => (
              <span key={m}><b>{MODE[m] || m}</b> {formatPaise(v)}</span>))}
              {Object.keys(summary.by_mode).length === 0 && <span className="muted">No bills</span>}</div>
          </div>
        </div>
      )}

      <div className="rep">
        <nav className="rep-nav" aria-label="Reports">
          {Object.entries(groups).map(([g, items]) => (
            <div key={g}>
              <h3>{g}</h3>
              {items.map((r) => <button key={r.name} className={name === r.name ? "active" : ""} onClick={() => { setName(r.name); setFind(""); }}>{r.label}</button>)}
            </div>
          ))}
        </nav>
        <section className="rep-main">
          {/* Phones: one grouped picker instead of the long list. */}
          <label className="rep-pick">
            <span>Report</span>
            <Select value={name} onChange={(e) => { setName(e.target.value); setFind(""); }}>
              {Object.entries(groups).map(([g, items]) => (
                <optgroup key={g} label={g}>
                  {items.map((r) => <option key={r.name} value={r.name}>{r.label}</option>)}
                </optgroup>
              ))}
            </Select>
          </label>
          {table && (
            <>
              <div className="rep-head">
                <div className="grow">
                  <h2>{table.title}</h2>
                  {table.note && <p className="muted small">{table.note}</p>}
                </div>
                {table.rows.length > 8 && <Input placeholder="Find in this report" value={find} onChange={(e) => setFind(e.target.value)} />}
                {table.rows.length > 1 && (
                  <label className="sort-by">
                    <span className="muted small">Sort by</span>
                    <Select value={sort ? `${sort.key}:${sort.desc ? "d" : "a"}` : ""} onChange={(e) => {
                      const v = e.target.value;
                      setSort(v ? { key: v.slice(0, -2), desc: v.endsWith(":d") } : null);
                    }}>
                      <option value="">As listed</option>
                      {table.columns.flatMap((c) => numeric(c.kind)
                        ? [<option key={c.key + "d"} value={`${c.key}:d`}>{c.label}: highest first</option>,
                           <option key={c.key + "a"} value={`${c.key}:a`}>{c.label}: lowest first</option>]
                        : [<option key={c.key + "a"} value={`${c.key}:a`}>{c.label}: {c.kind.startsWith("date") || c.kind === "time" ? "oldest first" : "A to Z"}</option>,
                           <option key={c.key + "d"} value={`${c.key}:d`}>{c.label}: {c.kind.startsWith("date") || c.kind === "time" ? "newest first" : "Z to A"}</option>])}
                    </Select>
                  </label>
                )}
                <button className="btn btn-ghost" onClick={download}>Download for Excel</button>
              </div>
              <div className={`rep-table-wrap ${loading ? "loading" : ""}`}>
                <table className="table rep-table">
                  <thead><tr>{table.columns.map((c) => (
                    <th key={c.key} className={numeric(c.kind) ? "num" : ""}>
                      <button onClick={() => setSort((s) => (s?.key === c.key ? { key: c.key, desc: !s.desc } : { key: c.key, desc: numeric(c.kind) }))}>
                        {c.label}{sort?.key === c.key ? (sort.desc ? " ↓" : " ↑") : ""}
                      </button>
                    </th>))}</tr></thead>
                  <tbody>
                    {shown.map((r, i) => (
                      <tr key={i}>{table.columns.map((c) => <td key={c.key} className={numeric(c.kind) ? "num" : ""}>{show(r[c.key], c.kind)}</td>)}</tr>
                    ))}
                    {rows.length === 0 && <tr><td colSpan={table.columns.length} className="muted">{find ? "Nothing matches." : "Nothing in this period."}</td></tr>}
                  </tbody>
                  {table.totals && !find && (
                    <tfoot><tr>{table.columns.map((c) => <td key={c.key} className={numeric(c.kind) ? "num" : ""}>{show(table.totals![c.key], c.kind)}</td>)}</tr></tfoot>
                  )}
                </table>
              </div>
              {rows.length > shown.length && <p className="muted small">Showing the first {shown.length} of {rows.length} rows. Download for all of them.</p>}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
