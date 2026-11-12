import { useEffect, useState } from "react";
import { api } from "../api";
import { useSession } from "../session";
import type { Brand, Tenant, UiTheme } from "../types";
import { THEMES } from "../themes";
import { Button, Card, ErrorText, Field, Input, useAction, Tab, Tabs } from "../ui";

export default function Branding() {
  const { me, refresh, chooseRestaurant } = useSession();
  const t = me?.tenant;
  const [form, setForm] = useState({ name: t?.name || "", app_name: t?.app_name || "", primary_color: t?.primary_color || "#2563eb", gstin: t?.gstin || "" });
  const save = useAction();
  const upload = useAction();
  const look = useAction();
  const [saved, setSaved] = useState(false);

  async function afterChange(updated: Tenant) {
    await refresh();
    await chooseRestaurant(updated.login_id); // reload public branding: colour, logo, manifest
  }

  return (
    <div className="stack">
      <h1>Branding</h1>
      <p className="muted">This is what your staff and customers see: on the login screen, the app, bills and the installed app icon.</p>
      <Tabs id="branding">
      <Tab label="Logo and look">
      <Card title="Logo">
        <div className="logo-row">
          {t?.logo_url ? <img className="logo-preview" src={t.logo_url} alt="Current logo" /> : <span className="logo-fallback big">{(t?.app_name || t?.name || "W")[0]}</span>}
          <div>
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              const fd = new FormData();
              fd.append("file", file);
              upload.run(async () => afterChange(await api<Tenant>("/tenant/branding/logo", { form: fd })));
              e.target.value = "";
            }} />
            <p className="muted small">PNG, JPG or WebP, under 1 MB. A square logo works best.</p>
            <ErrorText error={upload.error} />
          </div>
        </div>
      </Card>
      <Card title="Look of the app">
        <p className="muted small">For the staff screens: counter, kitchen, setup. Your logo and brand colour stay in every look. Customer screens and printed bills don't change.</p>
        <div className="theme-picks">
          {THEMES.map((th) => (
            <button key={th.id} type="button" className={`theme-pick ${(t?.ui_theme || "steel") === th.id ? "on" : ""}`} aria-pressed={(t?.ui_theme || "steel") === th.id}
              disabled={look.busy} onClick={() => look.run(async () => afterChange(await api<Tenant>("/tenant/branding", { method: "PATCH", body: { ui_theme: th.id as UiTheme } })))}>
              <img src={th.preview} alt="" />
              <strong>{th.name}</strong>
              <span>{th.about}</span>
            </button>
          ))}
        </div>
        <ErrorText error={look.error} />
      </Card>
      </Tab>
      <Tab label="Name and colour"><Card title="Name and colour">
        <form onSubmit={(e) => {
          e.preventDefault();
          setSaved(false);
          save.run(async () => {
            await afterChange(await api<Tenant>("/tenant/branding", { method: "PATCH", body: { ...form, gstin: form.gstin || null } }));
            setSaved(true);
          });
        }}>
          <Field label="Restaurant name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="App name" hint="Shown on the login screen and under the app icon"><Input value={form.app_name} onChange={(e) => setForm({ ...form, app_name: e.target.value })} /></Field>
          <Field label="Brand colour">
            <div className="row">
              <input type="color" value={form.primary_color} onChange={(e) => setForm({ ...form, primary_color: e.target.value })} />
              <Input value={form.primary_color} onChange={(e) => setForm({ ...form, primary_color: e.target.value })} />
            </div>
          </Field>
          <Field label="GSTIN (if one GSTIN for all outlets)"><Input value={form.gstin} onChange={(e) => setForm({ ...form, gstin: e.target.value.toUpperCase() })} /></Field>
          <ErrorText error={save.error} />
          <Button disabled={save.busy}>Save</Button>
          {saved && <span className="ok">Saved</span>}
        </form>
      </Card></Tab>
      <Tab label="Brands"><Brands /></Tab>
      </Tabs>
    </div>
  );
}

function Brands() {
  const [brands, setBrands] = useState<Brand[]>([]);
  const [name, setName] = useState("");
  const { busy, error, run } = useAction();
  const load = () => api<Brand[]>("/tenant/brands").then(setBrands);
  useEffect(() => { load(); }, []);

  return (
    <Card title="Brands" actions={<span className="muted small">e.g. Shakewallah, Icecreamwallah</span>}>
      <ul className="list">
        {brands.map((b) => (
          <li key={b.id} className="list-row">
            {b.logo_url ? <img className="logo-sm" src={b.logo_url} alt="" /> : <span className="logo-fallback">{b.name[0]}</span>}
            <span className="grow">{b.name}</span>
            <label className="link">
              Upload logo
              <input hidden type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                const fd = new FormData();
                fd.append("file", f);
                run(async () => { await api(`/tenant/brands/${b.id}/logo`, { form: fd }); await load(); });
              }} />
            </label>
          </li>
        ))}
      </ul>
      <form className="row" onSubmit={(e) => {
        e.preventDefault();
        run(async () => { await api("/tenant/brands", { body: { name } }); setName(""); await load(); });
      }}>
        <Input placeholder="New brand name" value={name} onChange={(e) => setName(e.target.value)} />
        <Button disabled={busy || !name.trim()}>Add brand</Button>
      </form>
      <ErrorText error={error} />
    </Card>
  );
}
