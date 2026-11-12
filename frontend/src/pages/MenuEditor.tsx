import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { formatPaise, inputToPaise, paiseToInput } from "../money";
import type { AddonGroup, Brand, BrandMenu, Category, FoodType, Item } from "../types";
import { Button, Card, ErrorText, Field, FoodDot, Input, Select, Toggle, useAction } from "../ui";
import { ItemArt } from "../art";

export default function MenuEditor() {
  const [brands, setBrands] = useState<Brand[]>([]);
  const [brandId, setBrandId] = useState<string>("");
  const [menu, setMenu] = useState<BrandMenu | null>(null);
  const [catId, setCatId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Item | "new" | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    api<Brand[]>("/tenant/brands").then((b) => { setBrands(b); if (b[0]) setBrandId(b[0].id); });
  }, []);

  const load = useCallback(async () => {
    if (!brandId) return;
    const m = await api<BrandMenu>(`/menu/brands/${brandId}`);
    setMenu(m);
    setCatId((c) => (c && m.categories.some((x) => x.id === c) ? c : m.categories[0]?.id ?? null));
  }, [brandId]);
  useEffect(() => { load(); }, [load]);

  const items = useMemo(() => {
    if (!menu) return [];
    const q = search.trim().toLowerCase();
    return menu.items.filter((i) => q ? i.name.toLowerCase().includes(q) || (i.short_code || "").toLowerCase().includes(q) : i.category_id === catId);
  }, [menu, catId, search]);

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Menu</h1>
        <div className="row">
          {brands.length > 1 && (
            <Select value={brandId} onChange={(e) => setBrandId(e.target.value)}>
              {brands.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </Select>
          )}
          <Link className="btn btn-ghost" to="/menu/import">Import from Excel</Link>
        </div>
      </div>
      {menu && (
        <div className="menu-layout">
          <aside>
            <Categories menu={menu} brandId={brandId} selected={catId} onSelect={(id) => { setCatId(id); setSearch(""); }} reload={load} />
          </aside>
          <div className="stack">
            <div className="row">
              <Input placeholder="Search all items or short codes" value={search} onChange={(e) => setSearch(e.target.value)} />
              {catId && !editing && <Button onClick={() => setEditing("new")}>Add item</Button>}
            </div>
            {editing && (
              <ItemForm
                key={editing === "new" ? "new" : editing.id}
                menu={menu}
                brandId={brandId}
                item={editing === "new" ? null : editing}
                defaultCategory={catId!}
                onDone={async () => { setEditing(null); await load(); }}
              />
            )}
            <Card>
              {items.length === 0 ? <p className="muted">{search ? "No matching items." : "No items in this category yet."}</p> : (
                <table className="table">
                  <thead><tr><th>Item</th><th>Code</th><th>Prep area</th><th>Variants</th><th /></tr></thead>
                  <tbody>
                    {items.map((i) => (
                      <tr key={i.id} className={i.is_active ? "" : "inactive"}>
                        <td><FoodDot type={i.food_type} /> {i.name}</td>
                        <td>{i.short_code || "—"}</td>
                        <td>{(menu.prep_areas || []).find((a) => a.id === i.prep_area_id)?.name || "Kitchen"}</td>
                        <td>{i.variants.filter((v) => v.is_active).map((v) => `${v.name} ${formatPaise(v.price_paise)}`).join(" · ")}</td>
                        <td><button className="link" onClick={() => setEditing(i)}>Edit</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
            <AddonGroups menu={menu} brandId={brandId} reload={load} />
          </div>
        </div>
      )}
    </div>
  );
}

function Categories({ menu, brandId, selected, onSelect, reload }: {
  menu: BrandMenu; brandId: string; selected: string | null; onSelect: (id: string) => void; reload: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const { busy, error, run } = useAction();
  const count = (c: Category) => menu.items.filter((i) => i.category_id === c.id).length;
  return (
    <Card title="Categories">
      <ul className="cat-list">
        {menu.categories.map((c) => (
          <li key={c.id}>
            <button className={`${selected === c.id ? "active" : ""} ${c.is_active ? "" : "inactive"}`} onClick={() => onSelect(c.id)}>
              <span>{c.parent_id ? "↳ " : ""}{c.name}</span><span className="muted">{count(c)}</span>
            </button>
            {selected === c.id && (
              <button className="link small" onClick={() => run(async () => {
                await api(`/menu/categories/${c.id}`, { method: "PATCH", body: { ...c, is_active: !c.is_active } });
                await reload();
              })}>{c.is_active ? "Hide category" : "Show category"}</button>
            )}
          </li>
        ))}
      </ul>
      <form className="row" onSubmit={(e) => {
        e.preventDefault();
        run(async () => {
          await api("/menu/categories", { body: { brand_id: brandId, name, sort_order: menu.categories.length } });
          setName("");
          await reload();
        });
      }}>
        <Input placeholder="New category" value={name} onChange={(e) => setName(e.target.value)} />
        <Button disabled={busy || !name.trim()}>Add</Button>
      </form>
      <ErrorText error={error} />
    </Card>
  );
}

interface VariantRow { id?: string; name: string; price: string; is_active: boolean }

function ItemForm({ menu, brandId, item, defaultCategory, onDone }: {
  menu: BrandMenu; brandId: string; item: Item | null; defaultCategory: string; onDone: () => Promise<void>;
}) {
  const defaultTax = menu.tax_groups.find((t) => t.is_default)?.id || null;
  const [f, setF] = useState({
    name: item?.name || "", category_id: item?.category_id || defaultCategory, short_code: item?.short_code || "",
    food_type: (item?.food_type || "veg") as FoodType, hsn_sac: item?.hsn_sac || "", tax_group_id: item ? item.tax_group_id : defaultTax,
    prep_area_id: item ? item.prep_area_id : null, description: item?.description || "", is_active: item?.is_active ?? true,
    addon_group_ids: item?.addon_group_ids || [],
  });
  const [variants, setVariants] = useState<VariantRow[]>(
    item ? item.variants.filter((v) => v.is_active).map((v) => ({ id: v.id, name: v.name, price: paiseToInput(v.price_paise), is_active: v.is_active }))
      : [{ name: "Regular", price: "", is_active: true }]
  );
  const { busy, error, setError, run } = useAction();
  const [photo, setPhoto] = useState<string | null>(null);
  const setV = (i: number, patch: Partial<VariantRow>) => setVariants(variants.map((v, j) => (j === i ? { ...v, ...patch } : v)));

  function submit() {
    const parsed = variants.map((v, i) => ({ id: v.id, name: v.name.trim(), price_paise: inputToPaise(v.price), sort_order: i, is_active: true, sku: null }));
    const bad = parsed.find((v) => v.price_paise === null || !v.name);
    if (bad) { setError(`Check the name and price of variant "${bad.name || "(blank)"}"`); return; }
    run(async () => {
      const body = { ...f, brand_id: brandId, short_code: f.short_code || null, hsn_sac: f.hsn_sac || null,
        description: f.description || null, variants: parsed };
      await api(item ? `/menu/items/${item.id}` : "/menu/items", { method: item ? "PUT" : "POST", body });
      await onDone();
    });
  }

  return (
    <Card title={item ? `Edit ${item.name}` : "New item"}>
      <form onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <div className="grid2">
          <Field label="Item name"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required autoFocus /></Field>
          <Field label="Category"><Select value={f.category_id} onChange={(e) => setF({ ...f, category_id: e.target.value })}>
            {menu.categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select></Field>
          <Field label="Short code" hint="For fast typing at the counter, e.g. OREO3"><Input value={f.short_code} onChange={(e) => setF({ ...f, short_code: e.target.value.toUpperCase() })} /></Field>
          <Field label="Type"><Select value={f.food_type} onChange={(e) => setF({ ...f, food_type: e.target.value as FoodType })}>
            <option value="veg">Veg</option><option value="non_veg">Non-veg</option><option value="egg">Egg</option>
          </Select></Field>
          <Field label="Tax group"><Select value={f.tax_group_id || ""} onChange={(e) => setF({ ...f, tax_group_id: e.target.value || null })}>
            <option value="">No tax</option>
            {menu.tax_groups.filter((t) => t.is_active).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </Select></Field>
          <Field label="HSN/SAC"><Input value={f.hsn_sac} maxLength={8} onChange={(e) => setF({ ...f, hsn_sac: e.target.value })} /></Field>
          <Field label="Preparation area" hint="Its KOT goes to this counter's printer and KDS screen">
            <Select value={f.prep_area_id || ""} onChange={(e) => setF({ ...f, prep_area_id: e.target.value || null })}>
              <option value="">Kitchen (default)</option>
              {(menu.prep_areas || []).filter((a) => a.is_active).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </Select>
          </Field>
        </div>
        <Field label="Description" hint="Shown on the QR menu and the kiosk"><Input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        {item && (
          <Field label="Photo" hint="Square photo works best, under 1 MB. Shown on the QR menu and the kiosk.">
            <div className="row">
              <ItemArt name={item.name} image={photo ?? item.image_url} size="sm" />
              <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                const fd = new FormData();
                fd.append("file", file);
                run(async () => { const it = await api<Item>(`/menu/items/${item.id}/image`, { form: fd }); setPhoto(it.image_url); });
              }} />
            </div>
          </Field>
        )}

        <h3>Variants and prices</h3>
        <p className="muted small">Each variant (300 ML, 1000 ML) is sold and stocked as its own SKU.</p>
        {variants.map((v, i) => (
          <div className="row" key={v.id || i}>
            <Input aria-label="Variant name" value={v.name} onChange={(e) => setV(i, { name: e.target.value })} />
            <Input aria-label="Price in rupees" inputMode="decimal" placeholder="Price ₹" value={v.price} onChange={(e) => setV(i, { price: e.target.value })} />
            {variants.length > 1 && <button type="button" className="link" onClick={() => setVariants(variants.filter((_, j) => j !== i))}>Remove</button>}
          </div>
        ))}
        <button type="button" className="link" onClick={() => setVariants([...variants, { name: "", price: "", is_active: true }])}>+ Add variant</button>

        {menu.addon_groups.length > 0 && (
          <Field label="Add-on groups">
            <div className="chips">
              {menu.addon_groups.map((g) => (
                <Toggle key={g.id} label={g.name} checked={f.addon_group_ids.includes(g.id)}
                  onChange={(on) => setF({ ...f, addon_group_ids: on ? [...f.addon_group_ids, g.id] : f.addon_group_ids.filter((x) => x !== g.id) })} />
              ))}
            </div>
          </Field>
        )}
        <Toggle checked={f.is_active} onChange={(v) => setF({ ...f, is_active: v })} label="On the menu" />
        <ErrorText error={error} />
        <div className="row">
          <Button disabled={busy}>Save item</Button>
          <Button type="button" variant="ghost" onClick={() => onDone()}>Cancel</Button>
        </div>
      </form>
    </Card>
  );
}

function AddonGroups({ menu, brandId, reload }: { menu: BrandMenu; brandId: string; reload: () => Promise<void> }) {
  const [editing, setEditing] = useState<AddonGroup | "new" | null>(null);
  return (
    <Card title="Add-on groups" actions={!editing && <Button variant="ghost" onClick={() => setEditing("new")}>New group</Button>}>
      {editing && <AddonGroupForm brandId={brandId} group={editing === "new" ? null : editing} onDone={async () => { setEditing(null); await reload(); }} />}
      <ul className="list">
        {menu.addon_groups.map((g) => (
          <li key={g.id} className={`list-row ${g.is_active ? "" : "inactive"}`}>
            <span className="grow"><strong>{g.name}</strong> <span className="muted small">pick {g.min_select}–{g.max_select}</span><br />
              <span className="muted small">{g.addons.filter((a) => a.is_active).map((a) => `${a.name} ${formatPaise(a.price_paise)}`).join(" · ")}</span>
            </span>
            <button className="link" onClick={() => setEditing(g)}>Edit</button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function AddonGroupForm({ brandId, group, onDone }: { brandId: string; group: AddonGroup | null; onDone: () => Promise<void> }) {
  const [name, setName] = useState(group?.name || "");
  const [min, setMin] = useState(group?.min_select ?? 0);
  const [max, setMax] = useState(group?.max_select ?? 1);
  const [rows, setRows] = useState(group ? group.addons.filter((a) => a.is_active).map((a) => ({ id: a.id, name: a.name, price: paiseToInput(a.price_paise) })) : [{ id: undefined as string | undefined, name: "", price: "" }]);
  const { busy, error, setError, run } = useAction();
  return (
    <form className="nested" onSubmit={(e) => {
      e.preventDefault();
      const addons = rows.filter((r) => r.name.trim()).map((r) => ({ id: r.id, name: r.name.trim(), price_paise: inputToPaise(r.price || "0"), is_active: true }));
      if (addons.some((a) => a.price_paise === null)) { setError("Check the add-on prices"); return; }
      run(async () => {
        await api(group ? `/menu/addon-groups/${group.id}` : "/menu/addon-groups", { method: group ? "PUT" : "POST",
          body: { brand_id: brandId, name, min_select: min, max_select: max, is_active: true, addons } });
        await onDone();
      });
    }}>
      <div className="grid2">
        <Field label="Group name"><Input value={name} onChange={(e) => setName(e.target.value)} required /></Field>
        <div className="row">
          <Field label="Min picks"><Input type="number" min={0} value={min} onChange={(e) => setMin(Number(e.target.value))} /></Field>
          <Field label="Max picks"><Input type="number" min={1} value={max} onChange={(e) => setMax(Number(e.target.value))} /></Field>
        </div>
      </div>
      {rows.map((r, i) => (
        <div className="row" key={r.id || i}>
          <Input placeholder="Add-on" value={r.name} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <Input placeholder="Price ₹" inputMode="decimal" value={r.price} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, price: e.target.value } : x)))} />
        </div>
      ))}
      <button type="button" className="link" onClick={() => setRows([...rows, { id: undefined, name: "", price: "" }])}>+ Add-on</button>
      <ErrorText error={error} />
      <div className="row">
        <Button disabled={busy}>Save group</Button>
        <Button type="button" variant="ghost" onClick={() => onDone()}>Cancel</Button>
      </div>
    </form>
  );
}
