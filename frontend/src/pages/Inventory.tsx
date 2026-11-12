import { useEffect, useMemo, useState } from "react";
import { api, newId } from "../api";
import { formatPaise, inputToPaise } from "../money";
import { useSession } from "../session";
import type { Outlet, OutletMenu } from "../types";
import { Button, Card, ErrorText, Field, Input, Select, useAction } from "../ui";

// ------------------------------------------------------------------ types and quantity helpers

type Unit = "g" | "ml" | "pc";
interface StockItem {
  id: string; name: string; kind: "raw" | "prep"; unit: Unit; category: string | null; purchase_units: { name: string; factor: number }[];
  default_supplier_id: string | null; min_level_m: number; reorder_qty_m: number; yield_m: number | null; made_on_sale?: boolean; is_active: boolean; last_cost_k?: number;
}
interface Supplier { id: string; name: string; phone: string | null; gstin: string | null; is_active: boolean }
interface Level { id: string; name: string; kind: string; unit: Unit; category: string | null; qty_m?: number; min_level_m?: number; low?: boolean; negative?: boolean; cost_k?: number; value_paise?: number; counted_at: string | null }
interface PurchaseRow { id: string; supplier: string | null; invoice_no: string | null; invoice_date: string; amount_paise: number; tax_paise: number; total_paise: number; lines: { name: string; qty_m: number; unit: string }[]; status: string }
interface CostRow { variant_id: string; item: string; variant: string; price_paise: number; net_price_paise: number; cost_paise: number | null; margin_bp: number | null; food_cost_bp: number | null; has_recipe: boolean; missing_cost: string[] }
interface ReorderGroup { supplier_id: string | null; supplier: string; phone: string | null; lines: { stock_item_id: string; name: string; unit: Unit; on_hand_m: number; min_level_m: number; order_m: number; in_purchase_unit: { unit: string; qty: number } | null }[] }

/** "12.5" -> 12500 (thousandths). null if not a number. */
export function parseQty(t: string): number | null {
  const s = t.trim().replace(/,/g, "");
  if (!/^\d*(\.\d{0,3})?$/.test(s) || s === "" || s === ".") return null;
  const [w, f = ""] = s.split(".");
  return Number(w || "0") * 1000 + Number((f + "000").slice(0, 3));
}
export function fmtQty(m: number, unit: string): string {
  const big = unit === "g" ? "kg" : unit === "ml" ? "l" : null;
  let v = m, u = unit;
  if (big && Math.abs(m) >= 1_000_000) { v = Math.round(m / 1000); u = big; }
  return `${String(Number((v / 1000).toFixed(3)))} ${u}`;
}
const qtyInput = (m: number) => String(Number((m / 1000).toFixed(3)));

const TABS = [
  ["stock", "Stock"], ["count", "Closing count"], ["purchases", "Purchases"], ["recipes", "Recipes"],
  ["prep", "Prep & wastage"], ["reorder", "To order"], ["costing", "Costing"], ["items", "Stock items"],
] as const;
type Tab = (typeof TABS)[number][0];

/** Stock: what's in, what the recipes used, what to order, what each item costs to make. */
export default function Inventory() {
  const { me, deviceOutletId, can } = useSession();
  const manage = can("inventory.manage");
  const costs = can("costing.view");
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [outletId, setOutletId] = useState(me?.outlet_id || deviceOutletId || "");
  const [tab, setTab] = useState<Tab>(manage ? "stock" : "count");
  const [items, setItems] = useState<StockItem[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [bump, setBump] = useState(0);
  const reload = () => setBump((n) => n + 1);

  useEffect(() => { api<Outlet[]>("/tenant/outlets").then((o) => { setOutlets(o); setOutletId((c) => c || o[0]?.id || ""); }); }, []);
  useEffect(() => {
    api<StockItem[]>("/inventory/items").then(setItems);
    if (manage) api<Supplier[]>("/inventory/suppliers").then(setSuppliers);
  }, [bump, manage]);

  const tabs = TABS.filter(([k]) => manage || k === "count").filter(([k]) => costs || k !== "costing");
  return (
    <div className="stack inv">
      <div className="row">
        <h1 className="grow">Stock</h1>
        {!me?.outlet_id && outlets.length > 1 && (
          <Select value={outletId} onChange={(e) => setOutletId(e.target.value)} aria-label="Outlet">
            {outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </Select>
        )}
      </div>
      {tabs.length > 1 && (
        <nav className="pill-tabs" aria-label="Stock sections">
          {tabs.map(([k, label]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{label}</button>)}
        </nav>
      )}
      {outletId && tab === "stock" && <StockTab outletId={outletId} costs={costs} bump={bump} onCount={() => setTab("count")} />}
      {outletId && tab === "count" && <CountTab outletId={outletId} items={items} costs={costs} onSaved={reload} />}
      {outletId && tab === "purchases" && <PurchasesTab outletId={outletId} items={items} suppliers={suppliers} onChange={reload} />}
      {outletId && tab === "recipes" && <RecipesTab outletId={outletId} items={items} costs={costs} />}
      {outletId && tab === "prep" && <PrepTab outletId={outletId} items={items} costs={costs} onSaved={reload} />}
      {outletId && tab === "reorder" && <ReorderTab outletId={outletId} bump={bump} />}
      {outletId && tab === "costing" && <CostingTab outletId={outletId} onRecipes={() => setTab("recipes")} />}
      {tab === "items" && <ItemsTab items={items} suppliers={suppliers} onChange={reload} />}
    </div>
  );
}

// ------------------------------------------------------------------ pick a stock item by typing

function ItemPicker({ items, onPick, placeholder = "Type to find a stock item", kinds }: { items: StockItem[]; onPick: (i: StockItem) => void; placeholder?: string; kinds?: ("raw" | "prep")[] }) {
  const [q, setQ] = useState("");
  const recent = useMemo(() => { try { return JSON.parse(localStorage.getItem("wise.inv_recent") || "[]") as string[]; } catch { return []; } }, [q]);
  const pool = items.filter((i) => i.is_active && (!kinds || kinds.includes(i.kind)));
  const hits = q.trim() ? pool.filter((i) => i.name.toLowerCase().includes(q.trim().toLowerCase())).slice(0, 8) : [];
  const pick = (i: StockItem) => {
    onPick(i); setQ("");
    try { localStorage.setItem("wise.inv_recent", JSON.stringify([i.id, ...recent.filter((x) => x !== i.id)].slice(0, 10))); } catch { /* */ }
  };
  const recents = recent.map((id) => pool.find((i) => i.id === id)).filter(Boolean) as StockItem[];
  return (
    <div className="picker">
      <Input value={q} placeholder={placeholder} onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && hits[0]) { e.preventDefault(); pick(hits[0]); } }} aria-label="Find a stock item" />
      {hits.length > 0 && <ul className="picker-hits">{hits.map((i) => <li key={i.id}><button type="button" onClick={() => pick(i)}>{i.name} <span className="muted small">{i.unit}{i.kind === "prep" ? " · made here" : ""}</span></button></li>)}</ul>}
      {!q && recents.length > 0 && <div className="chips">{recents.slice(0, 6).map((i) => <button type="button" key={i.id} className="choice" onClick={() => pick(i)}>+ {i.name}</button>)}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ stock on hand

function StockTab({ outletId, costs, bump, onCount }: { outletId: string; costs: boolean; bump: number; onCount: () => void }) {
  const [data, setData] = useState<{ items: Level[]; value_paise?: number } | null>(null);
  const [only, setOnly] = useState<"all" | "low">("all");
  useEffect(() => { api<{ items: Level[]; value_paise?: number }>(`/inventory/stock?outlet_id=${outletId}`).then(setData); }, [outletId, bump]);
  if (!data) return <p className="muted">Loading…</p>;
  const low = data.items.filter((i) => i.low || i.negative);
  const shown = only === "low" ? low : data.items;
  if (!data.items.length) return <Card><p>No stock items yet. Add milk, sugar, cups and the rest under <strong>Stock items</strong>, then enter a purchase.</p></Card>;
  return (
    <>
      <div className="stat-row">
        {costs && <div className="stat"><span>Stock value</span><strong>{formatPaise(data.value_paise ?? 0)}</strong></div>}
        <div className="stat"><span>Low or out</span><strong className={low.length ? "error" : ""}>{low.length}</strong></div>
        <div className="stat"><span>Items</span><strong>{data.items.length}</strong></div>
        <Button variant="ghost" onClick={onCount}>Enter closing count</Button>
      </div>
      <div className="seg two"><button className={only === "all" ? "active" : ""} onClick={() => setOnly("all")}>All</button><button className={only === "low" ? "active" : ""} onClick={() => setOnly("low")}>Low or out ({low.length})</button></div>
      <Card>
        <table className="table">
          <thead><tr><th>Item</th><th className="num">On hand</th><th className="num">Minimum</th>{costs && <th className="num">Value</th>}<th>Last counted</th></tr></thead>
          <tbody>
            {shown.map((i) => (
              <tr key={i.id} className={i.negative ? "row-bad" : i.low ? "row-warn" : ""}>
                <td>{i.name}{i.kind === "prep" && <span className="badge">made here</span>}{i.category && <div className="muted small">{i.category}</div>}</td>
                <td className="num">{fmtQty(i.qty_m ?? 0, i.unit)}{i.negative && <div className="small error">below zero: count it</div>}{!i.negative && i.low && <div className="small error">low</div>}</td>
                <td className="num">{i.min_level_m ? fmtQty(i.min_level_m, i.unit) : "–"}</td>
                {costs && <td className="num">{formatPaise(i.value_paise ?? 0)}</td>}
                <td className="small muted">{i.counted_at ? new Date(i.counted_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "never"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}

// ------------------------------------------------------------------ closing count (blind for staff)

function CountTab({ outletId, items, costs, onSaved }: { outletId: string; items: StockItem[]; costs: boolean; onSaved: () => void }) {
  const [vals, setVals] = useState<Record<string, string>>({});
  const [q, setQ] = useState("");
  const [result, setResult] = useState<{ saved: number; lines?: { name: string; unit: Unit; expected_m: number; counted_m: number; diff_m: number; value_paise: number }[]; short_value_paise?: number } | null>(null);
  const { busy, error, run } = useAction();
  const active = items.filter((i) => i.is_active && (!q || i.name.toLowerCase().includes(q.toLowerCase())));
  const groups = useMemo(() => {
    const g = new Map<string, StockItem[]>();
    active.forEach((i) => { const k = i.category || "Other"; g.set(k, [...(g.get(k) || []), i]); });
    return [...g.entries()];
  }, [active]);
  const filled = Object.entries(vals).filter(([, v]) => v.trim() !== "");
  const bad = filled.filter(([, v]) => parseQty(v) === null);
  const save = () => run(async () => {
    const lines = filled.map(([id, v]) => ({ stock_item_id: id, counted_m: parseQty(v)! }));
    const r = await api<typeof result>("/inventory/counts", { body: { outlet_id: outletId, lines } });
    setResult(r); setVals({}); onSaved();
  });
  if (result) {
    return (
      <Card title="Count saved">
        <p>{result.saved} item{result.saved === 1 ? "" : "s"} counted. Stock is now what you counted.</p>
        {result.lines && <>
          {result.short_value_paise ? <p className="error">Short by {formatPaise(result.short_value_paise)} at cost.</p> : <p className="ok">Nothing short.</p>}
          <table className="table"><thead><tr><th>Item</th><th className="num">Expected</th><th className="num">Counted</th><th className="num">Difference</th>{costs && <th className="num">Value</th>}</tr></thead>
            <tbody>{result.lines.map((l) => <tr key={l.name} className={l.diff_m < 0 ? "row-warn" : ""}><td>{l.name}</td><td className="num">{fmtQty(l.expected_m, l.unit)}</td><td className="num">{fmtQty(l.counted_m, l.unit)}</td><td className="num">{l.diff_m ? fmtQty(l.diff_m, l.unit) : "–"}</td>{costs && <td className="num">{formatPaise(l.value_paise)}</td>}</tr>)}</tbody></table>
        </>}
        <Button onClick={() => setResult(null)}>Count more</Button>
      </Card>
    );
  }
  return (
    <Card title="Closing count">
      <p className="muted small">Count what's actually there and type it in the unit shown. Leave an item empty if you didn't count it.</p>
      <Input placeholder="Find an item" value={q} onChange={(e) => setQ(e.target.value)} />
      {groups.map(([cat, list]) => (
        <div key={cat} className="count-group">
          <h3>{cat}</h3>
          {list.map((i) => (
            <label key={i.id} className="count-line">
              <span className="grow">{i.name}</span>
              <input className="input num" inputMode="decimal" value={vals[i.id] ?? ""} placeholder="0" aria-label={`Count of ${i.name} in ${i.unit}`}
                onChange={(e) => setVals((v) => ({ ...v, [i.id]: e.target.value }))} />
              <span className="unit">{i.unit}</span>
            </label>
          ))}
        </div>
      ))}
      {items.length === 0 && <p className="muted">No stock items yet.</p>}
      {bad.length > 0 && <p className="error">Check the numbers: use digits, at most 3 decimals.</p>}
      <ErrorText error={error} />
      <Button disabled={busy || !filled.length || bad.length > 0} onClick={save}>Save count of {filled.length} item{filled.length === 1 ? "" : "s"}</Button>
    </Card>
  );
}

// ------------------------------------------------------------------ purchases

const STD: Record<Unit, { name: string; factor: number }[]> = { g: [{ name: "kg", factor: 1000 }], ml: [{ name: "l", factor: 1000 }], pc: [{ name: "dozen", factor: 12 }] };
const unitsOf = (i: StockItem) => [{ name: i.unit, factor: 1 }, ...STD[i.unit], ...(i.purchase_units || [])];

interface PLine { key: string; item: StockItem; qty: string; unit: string; amount: string; gst: string }

function PurchasesTab({ outletId, items, suppliers, onChange }: { outletId: string; items: StockItem[]; suppliers: Supplier[]; onChange: () => void }) {
  const [list, setList] = useState<PurchaseRow[]>([]);
  const [supplierId, setSupplierId] = useState("");
  const [newSupplier, setNewSupplier] = useState("");
  const [invoice, setInvoice] = useState("");
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [lines, setLines] = useState<PLine[]>([]);
  const [saved, setSaved] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  const load = () => api<PurchaseRow[]>(`/inventory/purchases?outlet_id=${outletId}`).then(setList);
  useEffect(() => { load(); }, [outletId]);

  const add = (i: StockItem) => {
    const big = unitsOf(i).reduce((a, b) => (b.factor > a.factor ? b : a));
    setLines((l) => [...l, { key: newId(), item: i, qty: "", unit: big.name, amount: "", gst: "0" }]);
    setSaved(null);
  };
  const set = (k: string, patch: Partial<PLine>) => setLines((l) => l.map((x) => (x.key === k ? { ...x, ...patch } : x)));
  const amount = lines.reduce((s, l) => s + (inputToPaise(l.amount || "0") ?? 0), 0);
  const tax = lines.reduce((s, l) => s + Math.floor(((inputToPaise(l.amount || "0") ?? 0) * Number(l.gst || 0) * 100 + 5000) / 10000), 0);
  const invalid = lines.some((l) => parseQty(l.qty) === null || !parseQty(l.qty) || inputToPaise(l.amount || "0") === null);

  const save = () => run(async () => {
    let sid = supplierId || null;
    if (supplierId === "_new") {
      if (!newSupplier.trim()) throw new Error("Type the supplier's name");
      sid = (await api<Supplier>("/inventory/suppliers", { body: { name: newSupplier.trim() } })).id;
    }
    await api("/inventory/purchases", { body: { id: newId(), outlet_id: outletId, supplier_id: sid, invoice_no: invoice || null, invoice_date: date,
      lines: lines.map((l) => ({ stock_item_id: l.item.id, qty_m: parseQty(l.qty)!, unit: l.unit, amount_paise: inputToPaise(l.amount || "0") ?? 0, gst_bp: Math.round(Number(l.gst || 0) * 100) })) } });
    setSaved(`Saved. Stock added for ${lines.length} item${lines.length === 1 ? "" : "s"}.`);
    setLines([]); setInvoice(""); setNewSupplier(""); onChange(); load();
  });

  return (
    <>
      <Card title="Enter a supplier's bill">
        <div className="grid2">
          <Field label="Supplier">
            <Select value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
              <option value="">No supplier / cash purchase</option>
              {suppliers.filter((s) => s.is_active).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              <option value="_new">+ New supplier…</option>
            </Select>
          </Field>
          {supplierId === "_new" && <Field label="New supplier's name"><Input value={newSupplier} onChange={(e) => setNewSupplier(e.target.value)} /></Field>}
          <Field label="Bill number"><Input value={invoice} onChange={(e) => setInvoice(e.target.value)} /></Field>
          <Field label="Bill date"><Input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        </div>
        <ItemPicker items={items} onPick={add} placeholder="Add an item from the bill: type milk, sugar…" />
        {lines.length > 0 && (
          <table className="table purchase-lines">
            <thead><tr><th>Item</th><th>Quantity</th><th>Amount before GST</th><th>GST %</th><th /></tr></thead>
            <tbody>
              {lines.map((l) => (
                <tr key={l.key}>
                  <td>{l.item.name}</td>
                  <td><div className="row nowrap"><input className="input num" inputMode="decimal" value={l.qty} onChange={(e) => set(l.key, { qty: e.target.value })} aria-label={`Quantity of ${l.item.name}`} />
                    <select className="input" value={l.unit} onChange={(e) => set(l.key, { unit: e.target.value })} aria-label="Unit">{unitsOf(l.item).map((u) => <option key={u.name} value={u.name}>{u.name}</option>)}</select></div></td>
                  <td><input className="input num" inputMode="decimal" placeholder="₹" value={l.amount} onChange={(e) => set(l.key, { amount: e.target.value })} aria-label={`Amount for ${l.item.name}`} /></td>
                  <td><select className="input" value={l.gst} onChange={(e) => set(l.key, { gst: e.target.value })} aria-label="GST rate">{["0", "5", "12", "18", "28"].map((g) => <option key={g} value={g}>{g}%</option>)}</select></td>
                  <td><button className="link danger" onClick={() => setLines((x) => x.filter((y) => y.key !== l.key))}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {lines.length > 0 && <p className="right">Before GST {formatPaise(amount)} · GST {formatPaise(tax)} · <strong>Total {formatPaise(amount + tax)}</strong></p>}
        <ErrorText error={error} />
        {saved && <p className="ok">{saved}</p>}
        <Button disabled={busy || !lines.length || invalid} onClick={save}>Save bill and add stock</Button>
      </Card>
      <Card title="Recent purchases">
        <table className="table">
          <thead><tr><th>Date</th><th>Supplier</th><th>Bill no.</th><th>Items</th><th className="num">Total</th><th /></tr></thead>
          <tbody>
            {list.map((p) => (
              <tr key={p.id} className={p.status === "cancelled" ? "muted" : ""}>
                <td>{p.invoice_date}</td><td>{p.supplier || "–"}</td><td>{p.invoice_no || "–"}</td>
                <td className="small">{p.lines.map((l) => `${l.name} ${String(Number((l.qty_m / 1000).toFixed(3)))} ${l.unit}`).join(", ")}</td>
                <td className="num">{formatPaise(p.total_paise)}</td>
                <td>{p.status === "cancelled" ? "Cancelled" : <button className="link danger" onClick={() => { if (confirm("Cancel this purchase? Its stock will be taken back out.")) api(`/inventory/purchases/${p.id}/cancel`, { method: "POST" }).then(() => { load(); onChange(); }); }}>Cancel</button>}</td>
              </tr>
            ))}
            {list.length === 0 && <tr><td colSpan={6} className="muted">No purchases yet.</td></tr>}
          </tbody>
        </table>
      </Card>
    </>
  );
}

// ------------------------------------------------------------------ recipes

type RLine = { stock_item_id: string; qty: string };
const sizeOf = (name: string) => { const m = name.match(/(\d+(?:\.\d+)?)\s*(ml|l|g|kg)?/i); if (!m) return null; const n = Number(m[1]); return /^(l|kg)$/i.test(m[2] || "") ? n * 1000 : n; };

function RecipesTab({ outletId, items, costs }: { outletId: string; items: StockItem[]; costs: boolean }) {
  const [menu, setMenu] = useState<OutletMenu | null>(null);
  const [addons, setAddons] = useState<{ id: string; name: string; group: string }[]>([]);
  const [all, setAll] = useState<Record<string, { stock_item_id: string; qty_m: number }[]>>({});
  const [kind, setKind] = useState<"variant" | "addon" | "prep">("variant");
  const [owner, setOwner] = useState<string>("");
  const [lines, setLines] = useState<RLine[]>([]);
  const [q, setQ] = useState("");
  const [copyTo, setCopyTo] = useState<string[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  const load = () => api<typeof all>("/inventory/recipes").then(setAll);
  useEffect(() => {
    api<OutletMenu>(`/menu/outlets/${outletId}`).then(setMenu);
    api<typeof addons>("/inventory/addons").then(setAddons);
    load();
  }, [outletId]);
  const byId = useMemo(() => Object.fromEntries(items.map((i) => [i.id, i])), [items]);
  const owners = useMemo(() => {
    if (kind === "variant") return (menu?.items || []).flatMap((it) => it.variants.map((v) => ({ id: v.variant_id, name: `${it.name}${v.name && v.name !== "Regular" ? " · " + v.name : ""}`, item: it.item_id, size: v.name })));
    if (kind === "addon") return addons.map((a) => ({ id: a.id, name: `${a.name} (${a.group})`, item: "", size: "" }));
    return items.filter((i) => i.kind === "prep").map((i) => ({ id: i.id, name: i.name, item: "", size: "" }));
  }, [kind, menu, addons, items]);
  const shown = owners.filter((o) => !q || o.name.toLowerCase().includes(q.toLowerCase()));
  const cur = owners.find((o) => o.id === owner);
  const open = (id: string) => {
    setOwner(id); setMsg(null); setCopyTo([]);
    setLines((all[`${kind}:${id}`] || []).map((l) => ({ stock_item_id: l.stock_item_id, qty: qtyInput(l.qty_m) })));
  };
  const cost = lines.reduce((s, l) => { const it = byId[l.stock_item_id]; const m = parseQty(l.qty) ?? 0; return s + (it?.last_cost_k ? Math.round((m * it.last_cost_k) / 1_000_000) : 0); }, 0);
  const prepItem = kind === "prep" ? byId[owner] : null;
  const siblings = kind === "variant" && cur ? owners.filter((o) => o.item === cur.item && o.id !== cur.id) : [];
  const ratio = (to: string) => { const a = sizeOf(cur?.size || ""), b = sizeOf(owners.find((o) => o.id === to)?.size || ""); return a && b ? b / a : 1; };

  const save = () => run(async () => {
    const body = { lines: lines.map((l) => ({ stock_item_id: l.stock_item_id, qty_m: parseQty(l.qty)! })) };
    await api(`/inventory/recipes/${kind}/${owner}`, { method: "PUT", body });
    for (const to of copyTo) await api("/inventory/recipes/copy", { body: { from_variant_id: owner, to_variant_ids: [to], factor_bp: Math.round(ratio(to) * 10000) } });
    await load();
    setMsg(copyTo.length ? `Saved, and copied to ${copyTo.length} other size${copyTo.length === 1 ? "" : "s"} (scaled by size).` : "Recipe saved. From now on every bill takes this from stock.");
    setCopyTo([]);
  });

  return (
    <div className="recipes">
      <Card>
        <div className="seg three">
          {(["variant", "addon", "prep"] as const).map((k) => <button key={k} className={kind === k ? "active" : ""} onClick={() => { setKind(k); setOwner(""); setLines([]); }}>{k === "variant" ? "Menu items" : k === "addon" ? "Add-ons" : "Made in-house"}</button>)}
        </div>
        <Input placeholder="Find" value={q} onChange={(e) => setQ(e.target.value)} />
        <ul className="recipe-owners">
          {shown.map((o) => {
            const has = (all[`${kind}:${o.id}`] || []).length;
            return <li key={o.id}><button className={o.id === owner ? "on" : ""} onClick={() => open(o.id)}><span className="grow">{o.name}</span>{has ? <span className="badge ok-badge">{has} item{has === 1 ? "" : "s"}</span> : <span className="badge">no recipe</span>}</button></li>;
          })}
          {shown.length === 0 && <li className="muted">{kind === "prep" ? "Add a stock item of type 'Made here' first." : "Nothing here."}</li>}
        </ul>
      </Card>
      <Card title={cur ? cur.name : "Pick something to give it a recipe"}>
        {cur && <>
          <p className="muted small">{kind === "prep" ? `One batch makes ${prepItem?.yield_m ? fmtQty(prepItem.yield_m, prepItem.unit) : "?"}. List what one batch uses.` : "What one of these uses, cup and lid included."}</p>
          {lines.map((l, idx) => {
            const it = byId[l.stock_item_id];
            return (
              <div key={l.stock_item_id} className="recipe-line">
                <span className="grow">{it?.name ?? "?"}</span>
                <input className="input num" inputMode="decimal" value={l.qty} aria-label={`${it?.name} quantity`} onChange={(e) => setLines((x) => x.map((y, i) => (i === idx ? { ...y, qty: e.target.value } : y)))} />
                <span className="unit">{it?.unit}</span>
                <button className="link danger" onClick={() => setLines((x) => x.filter((_, i) => i !== idx))} aria-label={`Remove ${it?.name}`}>✕</button>
              </div>
            );
          })}
          <ItemPicker items={items.filter((i) => !lines.some((l) => l.stock_item_id === i.id) && i.id !== owner)} onPick={(i) => setLines((x) => [...x, { stock_item_id: i.id, qty: "" }])} placeholder="Add an ingredient: type to search" />
          {costs && lines.length > 0 && <p className="muted">Cost at last purchase prices: <strong>{formatPaise(cost)}</strong></p>}
          {siblings.length > 0 && (
            <div className="copy-to">
              <span className="small">Also copy to:</span>
              {siblings.map((s) => <button key={s.id} className={`choice ${copyTo.includes(s.id) ? "on" : ""}`} onClick={() => setCopyTo((c) => (c.includes(s.id) ? c.filter((x) => x !== s.id) : [...c, s.id]))}>{s.size} ×{Number(ratio(s.id).toFixed(2))}</button>)}
            </div>
          )}
          <ErrorText error={error} />
          {msg && <p className="ok">{msg}</p>}
          <Button disabled={busy || lines.some((l) => !parseQty(l.qty))} onClick={save}>Save recipe</Button>
        </>}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ prep and wastage

function PrepTab({ outletId, items, costs, onSaved }: { outletId: string; items: StockItem[]; costs: boolean; onSaved: () => void }) {
  const [prepId, setPrepId] = useState("");
  const [made, setMade] = useState("");
  const [waste, setWaste] = useState<{ item: StockItem; qty: string; reason: string }[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const prep = items.filter((i) => i.kind === "prep" && i.is_active && !i.made_on_sale);
  const p = items.find((i) => i.id === prepId);
  const a = useAction(), b = useAction();
  return (
    <div className="grid2 wide">
      <Card title="Made in-house">
        <p className="muted small">E.g. dry fruit crush from badam and pista. The ingredients come out of stock and the crush goes in.</p>
        <Field label="What was made"><Select value={prepId} onChange={(e) => setPrepId(e.target.value)}><option value="">Pick…</option>{prep.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}</Select></Field>
        {p && <Field label={`How much (${p.unit})`} hint={p.yield_m ? `One batch makes ${fmtQty(p.yield_m, p.unit)}` : undefined}><Input inputMode="decimal" value={made} onChange={(e) => setMade(e.target.value)} /></Field>}
        <ErrorText error={a.error} />
        <Button disabled={a.busy || !p || !parseQty(made)} onClick={() => a.run(async () => {
          const r = await api<{ cost_paise?: number }>("/inventory/prep", { body: { outlet_id: outletId, stock_item_id: prepId, made_m: parseQty(made) } });
          setMsg(`${p!.name}: ${fmtQty(parseQty(made)!, p!.unit)} added${costs && r.cost_paise != null ? `, cost ${formatPaise(r.cost_paise)}` : ""}.`); setMade(""); onSaved();
        })}>Save</Button>
        {prep.length === 0 && <p className="muted small">Add a stock item with type "Made here" and give it a recipe first.</p>}
      </Card>
      <Card title="Wastage">
        <p className="muted small">Spilt, expired or thrown away. Entered here, it isn't counted as missing later.</p>
        {waste.map((w, i) => (
          <div key={w.item.id} className="recipe-line">
            <span className="grow">{w.item.name}</span>
            <input className="input num" inputMode="decimal" value={w.qty} aria-label={`${w.item.name} wasted`} onChange={(e) => setWaste((x) => x.map((y, j) => (j === i ? { ...y, qty: e.target.value } : y)))} />
            <span className="unit">{w.item.unit}</span>
            <input className="input" placeholder="Why" value={w.reason} aria-label="Reason" onChange={(e) => setWaste((x) => x.map((y, j) => (j === i ? { ...y, reason: e.target.value } : y)))} />
          </div>
        ))}
        <ItemPicker items={items.filter((i) => !waste.some((w) => w.item.id === i.id))} onPick={(i) => setWaste((x) => [...x, { item: i, qty: "", reason: "" }])} placeholder="Add what was wasted" />
        <ErrorText error={b.error} />
        <Button disabled={b.busy || !waste.length || waste.some((w) => !parseQty(w.qty) || w.reason.trim().length < 2)} onClick={() => b.run(async () => {
          await api("/inventory/wastage", { body: { outlet_id: outletId, lines: waste.map((w) => ({ stock_item_id: w.item.id, qty_m: parseQty(w.qty), reason: w.reason })) } });
          setMsg(`Wastage of ${waste.length} item${waste.length === 1 ? "" : "s"} saved.`); setWaste([]); onSaved();
        })}>Save wastage</Button>
      </Card>
      {msg && <p className="ok">{msg}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ reorder list

function ReorderTab({ outletId, bump }: { outletId: string; bump: number }) {
  const [groups, setGroups] = useState<ReorderGroup[] | null>(null);
  const [qty, setQty] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState<string | null>(null);
  useEffect(() => { api<ReorderGroup[]>(`/inventory/reorder?outlet_id=${outletId}`).then((g) => {
    setGroups(g);
    setQty(Object.fromEntries(g.flatMap((x) => x.lines.map((l) => [l.stock_item_id, l.in_purchase_unit ? `${l.in_purchase_unit.qty} ${l.in_purchase_unit.unit}` : fmtQty(l.order_m, l.unit)]))));
  }); }, [outletId, bump]);
  if (!groups) return <p className="muted">Loading…</p>;
  if (!groups.length) return <Card><p className="ok">Nothing is below its minimum. Set minimums under <strong>Stock items</strong> to get this list.</p></Card>;
  const text = (g: ReorderGroup) => `Order from ${g.supplier}:\n` + g.lines.map((l) => `- ${l.name}: ${qty[l.stock_item_id] || ""}`).join("\n");
  return (
    <>
      <p className="muted small">At or below the minimum. Change the quantities if you like, then copy the list into WhatsApp.</p>
      {groups.map((g) => (
        <Card key={g.supplier_id || "none"} title={g.supplier} actions={<Button variant="ghost" onClick={() => { navigator.clipboard?.writeText(text(g)).then(() => setCopied(g.supplier)).catch(() => setCopied(null)); }}>{copied === g.supplier ? "Copied" : "Copy order"}</Button>}>
          <table className="table">
            <thead><tr><th>Item</th><th className="num">On hand</th><th className="num">Minimum</th><th>Order</th></tr></thead>
            <tbody>{g.lines.map((l) => <tr key={l.stock_item_id}><td>{l.name}</td><td className={`num ${l.on_hand_m < 0 ? "error" : ""}`}>{fmtQty(l.on_hand_m, l.unit)}</td><td className="num">{fmtQty(l.min_level_m, l.unit)}</td>
              <td><input className="input" value={qty[l.stock_item_id] ?? ""} onChange={(e) => setQty((q) => ({ ...q, [l.stock_item_id]: e.target.value }))} aria-label={`Order of ${l.name}`} /></td></tr>)}</tbody>
          </table>
          {g.phone && <p className="small muted">Supplier phone {g.phone}</p>}
        </Card>
      ))}
    </>
  );
}

// ------------------------------------------------------------------ costing

function CostingTab({ outletId, onRecipes }: { outletId: string; onRecipes: () => void }) {
  const [rows, setRows] = useState<CostRow[] | null>(null);
  const [sort, setSort] = useState<"margin" | "name" | "cost">("margin");
  useEffect(() => { api<CostRow[]>(`/inventory/costing?outlet_id=${outletId}`).then(setRows); }, [outletId]);
  if (!rows) return <p className="muted">Loading…</p>;
  const withR = rows.filter((r) => r.has_recipe), without = rows.filter((r) => !r.has_recipe);
  const sorted = [...withR].sort((a, b) => sort === "name" ? a.item.localeCompare(b.item) : sort === "cost" ? (b.food_cost_bp ?? 0) - (a.food_cost_bp ?? 0) : (a.margin_bp ?? 0) - (b.margin_bp ?? 0));
  return (
    <>
      <div className="row">
        <p className="muted small grow">Cost of one, from its recipe at average purchase prices, against the price before GST. Lowest margin first.</p>
        <Select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} aria-label="Sort costing"><option value="margin">Lowest margin first</option><option value="cost">Highest food cost first</option><option value="name">By name</option></Select>
      </div>
      <Card>
        <table className="table">
          <thead><tr><th>Item</th><th className="num">Price before GST</th><th className="num">Cost</th><th className="num">Profit</th><th className="num">Food cost</th></tr></thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.variant_id} className={(r.food_cost_bp ?? 0) > 3500 ? "row-warn" : ""}>
                <td>{r.item} <span className="muted">{r.variant !== "Regular" ? r.variant : ""}</span>{r.missing_cost.length > 0 && <div className="small error">{r.missing_cost.length} ingredient{r.missing_cost.length === 1 ? "" : "s"} without a purchase price</div>}</td>
                <td className="num">{formatPaise(r.net_price_paise)}</td>
                <td className="num">{formatPaise(r.cost_paise ?? 0)}</td>
                <td className="num">{formatPaise(r.net_price_paise - (r.cost_paise ?? 0))}</td>
                <td className={`num ${(r.food_cost_bp ?? 0) > 3500 ? "error" : ""}`}>{r.food_cost_bp == null ? "–" : `${(r.food_cost_bp / 100).toFixed(1)}%`}</td>
              </tr>
            ))}
            {sorted.length === 0 && <tr><td colSpan={5} className="muted">No recipes yet.</td></tr>}
          </tbody>
        </table>
      </Card>
      {without.length > 0 && <p className="muted small">{without.length} item size{without.length === 1 ? "" : "s"} without a recipe: their sales don't take anything from stock. <button className="link" onClick={onRecipes}>Add recipes</button></p>}
      <PrepCosts outletId={outletId} />
    </>
  );
}

// ------------------------------------------------------------------ stock items

const blank = (): StockItem => ({ id: "", name: "", kind: "raw", unit: "g", category: "", purchase_units: [], default_supplier_id: null, min_level_m: 0, reorder_qty_m: 0, yield_m: null, made_on_sale: false, is_active: true });

function ItemsTab({ items, suppliers, onChange }: { items: StockItem[]; suppliers: Supplier[]; onChange: () => void }) {
  const [edit, setEdit] = useState<StockItem | null>(null);
  const [min, setMin] = useState(""), [re, setRe] = useState(""), [yld, setYld] = useState("");
  const [q, setQ] = useState("");
  const { busy, error, run } = useAction();
  const start = (i: StockItem) => { setEdit({ ...i, purchase_units: [...(i.purchase_units || [])] }); setMin(i.min_level_m ? qtyInput(i.min_level_m) : ""); setRe(i.reorder_qty_m ? qtyInput(i.reorder_qty_m) : ""); setYld(i.yield_m ? qtyInput(i.yield_m) : ""); };
  const save = () => run(async () => {
    const e = edit!;
    const body = { name: e.name, kind: e.kind, unit: e.unit, category: e.category || null, purchase_units: e.purchase_units.filter((u) => u.name.trim() && u.factor > 0),
      default_supplier_id: e.default_supplier_id || null, min_level_m: parseQty(min || "0") ?? 0, reorder_qty_m: parseQty(re || "0") ?? 0,
      yield_m: e.kind === "prep" ? parseQty(yld || "0") || null : null, made_on_sale: e.kind === "prep" && !!e.made_on_sale, is_active: e.is_active };
    await api(e.id ? `/inventory/items/${e.id}` : "/inventory/items", { method: e.id ? "PATCH" : "POST", body });
    setEdit(null); onChange();
  });
  const cats = [...new Set(items.map((i) => i.category).filter(Boolean))] as string[];
  const shown = items.filter((i) => !q || i.name.toLowerCase().includes(q.toLowerCase()));
  if (edit) {
    return (
      <Card title={edit.id ? `Edit ${edit.name}` : "New stock item"}>
        <div className="grid2">
          <Field label="Name"><Input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} autoFocus /></Field>
          <Field label="Type"><Select value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value as "raw" | "prep" })}><option value="raw">Bought</option><option value="prep">Made here (from other items)</option></Select></Field>
          <Field label="Counted in" hint="Recipes and counts use this unit"><Select value={edit.unit} onChange={(e) => setEdit({ ...edit, unit: e.target.value as Unit })}><option value="g">grams (g)</option><option value="ml">millilitres (ml)</option><option value="pc">pieces (pc)</option></Select></Field>
          <Field label="Category" hint="Groups the closing count, e.g. Dairy, Packaging"><Input list="inv-cats" value={edit.category || ""} onChange={(e) => setEdit({ ...edit, category: e.target.value })} /><datalist id="inv-cats">{cats.map((c) => <option key={c} value={c} />)}</datalist></Field>
          <Field label={`Minimum to keep (${edit.unit})`} hint="Shows on the To order list at or below this"><Input inputMode="decimal" value={min} onChange={(e) => setMin(e.target.value)} /></Field>
          <Field label={`Usual order (${edit.unit})`} hint="Empty: enough to reach twice the minimum"><Input inputMode="decimal" value={re} onChange={(e) => setRe(e.target.value)} /></Field>
          <Field label="Usual supplier"><Select value={edit.default_supplier_id || ""} onChange={(e) => setEdit({ ...edit, default_supplier_id: e.target.value || null })}><option value="">–</option>{suppliers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></Field>
          {edit.kind === "prep" && <Field label={`One batch makes (${edit.unit})`} hint="What comes out, after cooking down or straining"><Input inputMode="decimal" value={yld} onChange={(e) => setYld(e.target.value)} /></Field>}
        </div>
        {edit.kind === "prep" && (
          <div className="seg two prep-mode" role="radiogroup" aria-label="How it's made">
            <button type="button" role="radio" aria-checked={!edit.made_on_sale} className={!edit.made_on_sale ? "active" : ""} onClick={() => setEdit({ ...edit, made_on_sale: false })}>
              <strong>Made in batches</strong><span>Sauce, base, crush: you enter each batch; sales take it from its own stock, and it's counted at closing.</span>
            </button>
            <button type="button" role="radio" aria-checked={!!edit.made_on_sale} className={edit.made_on_sale ? "active" : ""} onClick={() => setEdit({ ...edit, made_on_sale: true })}>
              <strong>Made fresh per order</strong><span>Mixed on the spot: no stock of its own; each sale takes its ingredients straight away.</span>
            </button>
          </div>
        )}
        <h3>Bought in</h3>
        <p className="muted small">{edit.unit === "g" ? "kg" : edit.unit === "ml" ? "litre" : "dozen"} works already. Add packs, e.g. "box of 24" = 24 {edit.unit}, or "crate" = 12000 ml.</p>
        {edit.purchase_units.map((u, i) => (
          <div key={i} className="row nowrap">
            <Input placeholder="Pack name" value={u.name} onChange={(e) => setEdit({ ...edit, purchase_units: edit.purchase_units.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} />
            <span>=</span>
            <input className="input num" inputMode="numeric" value={u.factor || ""} onChange={(e) => setEdit({ ...edit, purchase_units: edit.purchase_units.map((x, j) => (j === i ? { ...x, factor: Number(e.target.value.replace(/\D/g, "")) } : x)) })} aria-label="How many in one pack" />
            <span>{edit.unit}</span>
            <button className="link danger" onClick={() => setEdit({ ...edit, purchase_units: edit.purchase_units.filter((_, j) => j !== i) })}>Remove</button>
          </div>
        ))}
        <button className="link" onClick={() => setEdit({ ...edit, purchase_units: [...edit.purchase_units, { name: "", factor: 0 }] })}>+ Add a pack size</button>
        {edit.id && <label className="toggle"><input type="checkbox" checked={edit.is_active} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} /><span>In use</span></label>}
        <ErrorText error={error} />
        <div className="row"><Button disabled={busy || !edit.name.trim()} onClick={save}>Save</Button><Button variant="ghost" onClick={() => setEdit(null)}>Cancel</Button></div>
      </Card>
    );
  }
  return (
    <Card title="Stock items" actions={<Button onClick={() => start(blank())}>New stock item</Button>}>
      <Input placeholder="Find" value={q} onChange={(e) => setQ(e.target.value)} />
      <table className="table">
        <thead><tr><th>Name</th><th>Unit</th><th>Category</th><th className="num">Minimum</th><th /></tr></thead>
        <tbody>
          {shown.map((i) => <tr key={i.id} className={i.is_active ? "" : "muted"}><td>{i.name}{i.kind === "prep" && <span className="badge">{i.made_on_sale ? "made per order" : "made here"}</span>}</td><td>{i.unit}</td><td>{i.category || "–"}</td><td className="num">{i.min_level_m ? fmtQty(i.min_level_m, i.unit) : "–"}</td><td><button className="link" onClick={() => start(i)}>Edit</button></td></tr>)}
          {items.length === 0 && <tr><td colSpan={5} className="muted">Start with what you buy: milk, sugar, ice cream, cups, lids, straws.</td></tr>}
        </tbody>
      </table>
    </Card>
  );
}

interface PrepCost { id: string; name: string; unit: Unit; yield_m: number | null; made_on_sale: boolean; has_recipe: boolean; source: "made" | "recipe";
  batch_cost_paise: number | null; per_unit: string; per_unit_paise: number; recipe_per_unit_paise: number; missing_cost: string[] }

/** What each sauce, chutney or base costs: the real average of batches made, or its recipe at today's prices. */
function PrepCosts({ outletId }: { outletId: string }) {
  const [rows, setRows] = useState<PrepCost[]>([]);
  useEffect(() => { api<PrepCost[]>(`/inventory/prep-costs?outlet_id=${outletId}`).then(setRows).catch(() => undefined); }, [outletId]);
  if (!rows.length) return null;
  return (
    <Card title="Sauces, bases and other prep">
      <p className="muted small">A menu item that uses one of these is costed with its price per {"kg / litre / piece"} below. Until a batch has been made here, that price comes from its recipe at today's ingredient prices; after that, from the real batches (what they actually used).</p>
      <table className="table">
        <thead><tr><th>Prep item</th><th className="num">One batch</th><th className="num">Batch cost</th><th className="num">Cost per unit</th><th>From</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.name}{r.made_on_sale && <span className="badge">made per order</span>}
                {!r.has_recipe && <div className="small error">No recipe yet</div>}
                {r.missing_cost.length > 0 && <div className="small error">No price yet for {r.missing_cost.join(", ")}</div>}</td>
              <td className="num">{r.yield_m ? fmtQty(r.yield_m, r.unit) : "–"}</td>
              <td className="num">{r.batch_cost_paise == null ? "–" : formatPaise(r.batch_cost_paise)}</td>
              <td className="num">{formatPaise(r.per_unit_paise)} / {r.per_unit}</td>
              <td className="small">{r.source === "made" ? <>Batches made{r.recipe_per_unit_paise && r.recipe_per_unit_paise !== r.per_unit_paise ? <span className="muted"> (recipe says {formatPaise(r.recipe_per_unit_paise)})</span> : null}</> : "Recipe"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
