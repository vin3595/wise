import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Brand, BrandMenu, PrepArea } from "../types";
import { Card, ErrorText, Input, Select } from "../ui";

/** Which items go to which preparation area: that area's KOT printer and kitchen screen. */
export default function PrintRouting({ areas }: { areas: PrepArea[] }) {
  const [brands, setBrands] = useState<Brand[]>([]);
  const [brandId, setBrandId] = useState("");
  const [menu, setMenu] = useState<BrandMenu | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [find, setFind] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => { api<Brand[]>("/tenant/brands").then((b) => { setBrands(b); setBrandId(b[0]?.id || ""); }).catch((e) => setError(e.message)); }, []);
  const load = () => { if (brandId) api<BrandMenu>(`/menu/brands/${brandId}`).then(setMenu).catch((e) => setError(e.message)); };
  useEffect(load, [brandId]); // eslint-disable-line react-hooks/exhaustive-deps

  const active = areas.filter((a) => a.is_active);
  const where = (id: string | null) => {
    const a = areas.find((x) => x.id === id);
    if (!a) return "Kitchen (default) · print station";
    return `${a.name} · ${a.printer_host ? `printer ${a.printer_host}` : "print station"}`;
  };
  const groups = useMemo(() => {
    if (!menu) return [];
    const q = find.trim().toLowerCase();
    return menu.categories.map((c) => ({ cat: c, items: menu.items.filter((i) => i.category_id === c.id && (!q || i.name.toLowerCase().includes(q))) }))
      .filter((g) => g.items.length);
  }, [menu, find]);

  async function assign(ids: string[], areaId: string, label: string) {
    try {
      await api("/menu/items/prep-area", { body: { item_ids: ids, prep_area_id: areaId || null } });
      setSaved(`${label} now print${ids.length === 1 ? "s" : ""} at ${where(areaId || null).split(" · ")[0]}`);
      setError(null);
      load();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  const areaSelect = (value: string, onChange: (v: string) => void, label: string) => (
    <Select value={value} aria-label={label} onChange={(e) => onChange(e.target.value)}>
      <option value="">Kitchen (default)</option>
      {active.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
    </Select>
  );

  return (
    <Card title="Which items print where">
      <p className="muted">Pick where each item is made. Its KOT prints on that area's printer and shows on that area's kitchen screen. Set a whole category at once, or open it to change single items.</p>
      <div className="row">
        {brands.length > 1 && <Select value={brandId} onChange={(e) => setBrandId(e.target.value)} aria-label="Brand">{brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</Select>}
        <Input placeholder="Find an item" value={find} onChange={(e) => setFind(e.target.value)} />
        {saved && <span className="ok">{saved}</span>}
      </div>
      <ErrorText error={error} />
      <ul className="route-list">
        {groups.map(({ cat, items }) => {
          const ids = new Set(items.map((i) => i.prep_area_id || ""));
          const common = ids.size === 1 ? [...ids][0] : "mixed";
          const isOpen = open === cat.id || !!find.trim();
          return (
            <li key={cat.id} className="route-cat">
              <div className="route-row">
                <button className="route-toggle" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : cat.id)}>
                  <span className="route-caret">{isOpen ? "▾" : "▸"}</span>
                  <strong>{cat.name}</strong> <span className="muted small">{items.length} items</span>
                </button>
                {common === "mixed" ? <span className="badge">different areas</span> : <span className="muted small route-where">{where(common || null)}</span>}
                <Select value={common === "mixed" ? "__" : common} aria-label={`Area for all of ${cat.name}`}
                  onChange={(e) => e.target.value !== "__" && assign(items.map((i) => i.id), e.target.value, `All ${cat.name}`)}>
                  {common === "mixed" && <option value="__">Set all to…</option>}
                  <option value="">Kitchen (default)</option>
                  {active.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </Select>
              </div>
              {isOpen && (
                <ul className="route-items">
                  {items.map((it) => (
                    <li key={it.id} className="route-row">
                      <span className="grow">{it.name}</span>
                      <span className="muted small route-where">{where(it.prep_area_id)}</span>
                      {areaSelect(it.prep_area_id || "", (v) => assign([it.id], v, it.name), `Area for ${it.name}`)}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
        {menu && groups.length === 0 && <li className="muted">{find ? "No item matches." : "No items yet. Add them under Menu."}</li>}
      </ul>
    </Card>
  );
}
