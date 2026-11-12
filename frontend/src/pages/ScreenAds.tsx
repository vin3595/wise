import { useEffect, useState } from "react";
import { api } from "../api";
import { Button, Card, ErrorText, Field, Input, Select, useAction } from "../ui";
import type { Outlet } from "../types";

interface Ad {
  id: string; outlet_id: string | null; kind: "image" | "video"; url: string; title: string | null; seconds: number; sort: number;
  starts_on: string | null; ends_on: string | null; is_active: boolean; showing_now: boolean;
}

/** Pictures and videos for the customer screen (the POS's second monitor) while the counter is idle. */
export default function ScreenAds() {
  const [ads, setAds] = useState<Ad[]>([]);
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [outletId, setOutletId] = useState("");
  const [seconds, setSeconds] = useState("8");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [inputKey, setInputKey] = useState(0);
  const load = useAction();
  const add = useAction();
  const reload = () => load.run(async () => setAds(await api<Ad[]>("/tenant/ads")));
  useEffect(() => { reload(); api<Outlet[]>("/tenant/outlets").then(setOutlets).catch(() => undefined); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const save = (a: Ad, patch: Partial<Ad>) => load.run(async () => {
    const n = { ...a, ...patch };
    await api(`/tenant/ads/${a.id}`, { method: "PUT", body: { title: n.title, outlet_id: n.outlet_id, seconds: n.seconds, sort: n.sort, starts_on: n.starts_on, ends_on: n.ends_on, is_active: n.is_active } });
    await reload();
  });
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= ads.length) return;
    const order = [...ads];
    [order[i], order[j]] = [order[j], order[i]];
    load.run(async () => {
      for (const [k, a] of order.entries()) if (a.sort !== k + 1) await api(`/tenant/ads/${a.id}`, { method: "PUT", body: { title: a.title, outlet_id: a.outlet_id, seconds: a.seconds, sort: k + 1, starts_on: a.starts_on, ends_on: a.ends_on, is_active: a.is_active } });
      await reload();
    });
  };
  const outletName = (id: string | null) => (id ? outlets.find((o) => o.id === id)?.name || "One outlet" : "All outlets");

  return (
    <div className="stack">
      <h1>Customer screen ads</h1>
      <p className="muted">These play on the customer-facing screen of the counter when nobody is being billed. As soon as the cashier adds an item, the screen shows the order; when the cashier taps "Ask customer on their screen", it shows the number pad. Customers can't touch anything else.</p>

      <Card title="Add a picture or video">
        <div className="grid2">
          <Field label="File" hint="Picture: PNG/JPG/WebP, up to 5 MB, landscape 1920×1080 (16:9) fills most screens. Video: MP4, up to 60 MB, plays without sound.">
            <input key={inputKey} type="file" accept="image/png,image/jpeg,image/webp,video/mp4,video/webm" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </Field>
          <Field label="Name (for you)"><Input value={title} maxLength={80} placeholder="e.g. Mango season" onChange={(e) => setTitle(e.target.value)} /></Field>
          <Field label="Show at">
            <Select value={outletId} onChange={(e) => setOutletId(e.target.value)}>
              <option value="">All outlets</option>
              {outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </Select>
          </Field>
          <Field label="Seconds on screen" hint="Pictures only. Videos play to the end.">
            <Input type="number" min={3} max={120} value={seconds} onChange={(e) => setSeconds(e.target.value)} />
          </Field>
          <Field label="From (optional)"><Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
          <Field label="Until (optional)" hint="It stops showing after this day by itself."><Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
        </div>
        <ErrorText error={add.error} />
        <Button disabled={!file || add.busy} onClick={() => add.run(async () => {
          const fd = new FormData();
          fd.append("file", file!);
          if (title.trim()) fd.append("title", title.trim());
          if (outletId) fd.append("outlet_id", outletId);
          fd.append("seconds", String(Math.max(3, Math.min(120, Number(seconds) || 8))));
          if (from) fd.append("starts_on", from);
          if (to) fd.append("ends_on", to);
          await api("/tenant/ads", { form: fd });
          setFile(null); setTitle(""); setFrom(""); setTo(""); setInputKey((k) => k + 1);
          await reload();
        })}>{add.busy ? "Uploading…" : "Add to the screen"}</Button>
      </Card>

      <Card title={`On the screen (${ads.filter((a) => a.showing_now).length} showing today)`}>
        <ErrorText error={load.error} />
        {ads.length === 0 && <p className="muted">No ads yet. Until you add one, the screen shows your logo and "Welcome".</p>}
        <ul className="ads-list">
          {ads.map((a, i) => (
            <li key={a.id} className={a.showing_now ? "" : "off"}>
              {a.kind === "image" ? <img src={a.url} alt="" /> : <video src={a.url} muted preload="metadata" />}
              <div className="ads-meta">
                <strong>{a.title || (a.kind === "video" ? "Video" : "Picture")}</strong>
                <span className="muted small">
                  {outletName(a.outlet_id)} · {a.kind === "image" ? `${a.seconds} s` : "video"}
                  {a.starts_on || a.ends_on ? ` · ${a.starts_on || "now"} to ${a.ends_on || "no end"}` : ""}
                  {a.showing_now ? "" : a.is_active ? " · not in its dates" : " · paused"}
                </span>
              </div>
              <div className="row">
                <button className="link" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">↑</button>
                <button className="link" disabled={i === ads.length - 1} onClick={() => move(i, 1)} aria-label="Move down">↓</button>
                <button className="link" onClick={() => save(a, { is_active: !a.is_active })}>{a.is_active ? "Pause" : "Show"}</button>
                <button className="link danger" onClick={() => { if (confirm(`Remove "${a.title || "this ad"}" from the screen?`)) load.run(async () => { await api(`/tenant/ads/${a.id}`, { method: "DELETE" }); await reload(); }); }}>Remove</button>
              </div>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
