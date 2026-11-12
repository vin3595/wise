import { useEffect, useState } from "react";
import { api } from "../api";
import type { OutletMenu } from "../types";
import { ErrorText } from "../ui";

/** Kitchen: mark items sold out for today (or back on). Counters, kiosk and QR menu follow at once. */
export default function SoldOutPanel({ outletId, onClose }: { outletId: string; onClose: () => void }) {
  const [menu, setMenu] = useState<OutletMenu | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<OutletMenu>(`/menu/outlets/${outletId}`).then(setMenu).catch((e) => setError(e.message)); }, [outletId]);
  const flip = async (itemId: string, vid: string, now: boolean) => {
    try {
      await api(`/menu/outlets/${outletId}/sold-out`, { body: { variant_ids: [vid], sold_out: !now } });
      setMenu((m) => m && { ...m, items: m.items.map((i) => i.item_id !== itemId ? i : { ...i, variants: i.variants.map((v) => v.variant_id === vid ? { ...v, sold_out: !now } : v) }) });
    } catch (e) { setError(e instanceof Error ? e.message : "Couldn't change it"); }
  };
  const items = (menu?.items || []).filter((i) => !q || i.name.toLowerCase().includes(q.toLowerCase()));
  const out = (menu?.items || []).flatMap((i) => i.variants.filter((v) => v.sold_out).map((v) => `${i.name}${i.variants.length > 1 ? ` ${v.name}` : ""}`));
  return (
    <div className="modal-back" role="dialog" aria-modal="true" aria-label="Sold out today">
      <div className="modal sold-panel">
        <div className="row"><h2 className="grow">Sold out today</h2><button className="btn btn-ghost" onClick={onClose}>Done</button></div>
        <p className="muted small">{out.length ? `Sold out: ${out.join(", ")}.` : "Nothing is sold out."} Everything comes back by itself tomorrow.</p>
        <input className="input" placeholder="Find an item" value={q} onChange={(e) => setQ(e.target.value)} />
        <ErrorText error={error} />
        <ul className="sold-list">
          {items.map((i) => (
            <li key={i.item_id}>
              <span className="grow">{i.name}</span>
              {i.variants.map((v) => (
                <button key={v.variant_id} className={`choice ${v.sold_out ? "on" : ""}`} aria-pressed={!!v.sold_out} onClick={() => flip(i.item_id, v.variant_id, !!v.sold_out)}>
                  {i.variants.length > 1 ? v.name : "Sold out"}{v.sold_out && i.variants.length > 1 ? " ✕" : ""}
                </button>
              ))}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
