import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { formatPaise, inputToPaise, paiseToInput } from "../money";
import type { Outlet, OutletMenu as Menu, OutletMenuVariant } from "../types";
import { Card, ErrorText, FoodDot, Input, Select, useAction } from "../ui";

/** Per-outlet price and availability on top of the brand's master menu. */
export default function OutletMenu() {
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [outletId, setOutletId] = useState("");
  const [menu, setMenu] = useState<Menu | null>(null);
  const { error, run } = useAction();

  useEffect(() => {
    api<Outlet[]>("/tenant/outlets").then((o) => { setOutlets(o); if (o[0]) setOutletId(o[0].id); });
  }, []);
  const load = useCallback(async () => {
    if (outletId) setMenu(await api<Menu>(`/menu/outlets/${outletId}?include_unavailable=true`));
  }, [outletId]);
  useEffect(() => { load(); }, [load]);

  async function save(v: OutletMenuVariant, patch: { is_active?: boolean | null; price_paise?: number | null }) {
    await run(async () => {
      const current = { is_active: v.available ? null : false, price_paise: v.price_paise !== v.master_price_paise ? v.price_paise : null };
      const next = { ...current, ...patch };
      if (next.price_paise === v.master_price_paise) next.price_paise = null;
      if (next.is_active === true) next.is_active = null;
      await api(`/menu/outlets/${outletId}/variants/${v.variant_id}`, { method: "PUT", body: next });
      await load();
    });
  }

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Outlet menu</h1>
        <Select value={outletId} onChange={(e) => setOutletId(e.target.value)}>
          {outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </Select>
      </div>
      <p className="muted">Switch items off or change prices for this outlet only. The brand's master menu stays the same.</p>
      <ErrorText error={error} />
      {menu?.categories.map((c) => {
        const items = menu.items.filter((i) => i.category_id === c.id);
        if (!items.length) return null;
        return (
          <Card key={c.id} title={c.name}>
            <table className="table">
              <thead><tr><th>Item</th><th>Variant</th><th>Master price</th><th>Outlet price</th><th>Selling here</th></tr></thead>
              <tbody>
                {items.flatMap((i) => i.variants.map((v) => (
                  <tr key={v.variant_id} className={v.available ? "" : "inactive"}>
                    <td><FoodDot type={i.food_type} /> {i.name}</td>
                    <td>{v.name}</td>
                    <td>{formatPaise(v.master_price_paise)}</td>
                    <td>
                      <PriceCell v={v} onSave={(p) => save(v, { price_paise: p })} />
                    </td>
                    <td>
                      <input type="checkbox" checked={v.available} aria-label={`Sell ${i.name} ${v.name} here`}
                        onChange={(e) => save(v, { is_active: e.target.checked ? null : false })} />
                    </td>
                  </tr>
                )))}
              </tbody>
            </table>
          </Card>
        );
      })}
    </div>
  );
}

function PriceCell({ v, onSave }: { v: OutletMenuVariant; onSave: (paise: number) => void }) {
  const [text, setText] = useState(paiseToInput(v.price_paise));
  useEffect(() => setText(paiseToInput(v.price_paise)), [v.price_paise]);
  const commit = () => {
    const p = inputToPaise(text);
    if (p === null) { setText(paiseToInput(v.price_paise)); return; }
    if (p !== v.price_paise) onSave(p);
  };
  return (
    <Input className={`input price ${v.price_paise !== v.master_price_paise ? "changed" : ""}`} inputMode="decimal" value={text}
      onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />
  );
}
