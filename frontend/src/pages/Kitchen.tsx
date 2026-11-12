import { useEffect, useState } from "react";
import { api } from "../api";
import { useSession } from "../session";
import type { Outlet, PrepArea } from "../types";
import { Button, Card, ErrorText, Input, Select, Toggle, useAction, Tab, Tabs } from "../ui";
import { QrImage } from "../qr";
import PrintRouting from "../components/PrintRouting";
import { createPortal } from "react-dom";

/** Preparation areas (each gets its own KOT and KDS screen) and the customer display links. */
export default function Kitchen() {
  const { can } = useSession();
  const [areas, setAreas] = useState<PrepArea[]>([]);
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [name, setName] = useState("");
  const { busy, error, run } = useAction();
  const load = async () => {
    const [a, o] = await Promise.all([api<PrepArea[]>("/kitchen/prep-areas"), api<Outlet[]>("/tenant/outlets")]);
    setAreas(a);
    setOutlets(o);
  };
  useEffect(() => { load(); }, []);

  // Customers' phones reach the restaurant through its public address (on a Wise Box: the Cloudflare
  // address), even when staff opened Wise on the shop Wi-Fi.
  const [customerBase, setCustomerBase] = useState(location.origin);
  useEffect(() => { api<{ customer_url: string | null }>("/public/config").then((c) => c.customer_url && setCustomerBase(c.customer_url)).catch(() => {}); }, []);
  const displayUrl = (o: Outlet) => `${location.origin}/display/${o.display_key}`;
  const qrUrl = (o: Outlet, table?: string) => `${customerBase}/q/${o.qr_key}${table ? `?t=${encodeURIComponent(table)}` : ""}`;
  const kioskUrl = (o: Outlet) => `${location.origin}/kiosk/${o.qr_key}`;
  const [sheet, setSheet] = useState<Outlet | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null); // two taps to make a new link
  const printUrl = (o: Outlet) => `${location.origin}/print/${o.print_key}`;
  const saveOutlet = (o: Outlet, patch: Partial<Outlet>) => run(async () => {
    await api(`/tenant/outlets/${o.id}`, { method: "PATCH", body: { ...o, ...patch } });
    await load();
  });

  return (
    <div className="stack">
      <h1>Kitchen setup</h1>
      <Tabs id="kitchen">
      <Tab label="Preparation areas">
      <Card title="Preparation areas">
        <p className="muted">Each item is made at one area. An order with a shake and fries makes two KOTs, one for each area, each printed on that area's printer, and each area's KDS screen shows only its own tickets. Set an item's area under Menu, or in the "Prep Area" column of the Excel import.</p>
        <ul className="list">
          {areas.map((a) => (
            <li key={a.id} className={`area-row ${a.is_active ? "" : "inactive"}`}>
              <div className="list-row">
                <span className="grow"><strong>{a.name}</strong><PrinterStatus area={a} printing={outlets.some((o) => o.print_kots)} /></span>
                {can("menu.manage") && <button className="link" onClick={() => setEditing(editing === a.id ? null : a.id)}>Printer</button>}
                {can("menu.manage") && (
                  <button className="link" onClick={() => run(async () => {
                    await api(`/kitchen/prep-areas/${a.id}`, { method: "PATCH", body: { ...a, is_active: !a.is_active } });
                    await load();
                  })}>{a.is_active ? "Switch off" : "Switch on"}</button>
                )}
              </div>
              {editing === a.id && <PrinterForm area={a} onSaved={async () => { setEditing(null); await load(); }} />}
            </li>
          ))}
          <li className="list-row muted"><span className="grow">Kitchen (default for items with no area; their KOTs print on the print station)</span></li>
        </ul>
        {can("menu.manage") && (
          <form className="row" onSubmit={(e) => {
            e.preventDefault();
            run(async () => { await api("/kitchen/prep-areas", { body: { name, sort_order: areas.length } }); setName(""); await load(); });
          }}>
            <Input placeholder="e.g. Shake counter, Ice cream, Pizza oven" value={name} onChange={(e) => setName(e.target.value)} />
            <Button disabled={busy || !name.trim()}>Add area</Button>
          </form>
        )}
        <ErrorText error={error} />
      </Card>
      </Tab>
      {can("menu.manage") && areas.length > 0 && <Tab label="Which items print where"><PrintRouting areas={areas} /></Tab>}

      {can("outlets.manage") && (
        <Tab label="KOT printing"><Card title="KOT printing">
          <p className="muted">Each KOT prints by itself on its area's printer, once. Reprints come out marked "Reprint", and if an order is cancelled after its KOT printed, the kitchen gets a "Cancelled" slip.</p>
          <ul className="print-ways">
            <li><strong>Network printers</strong> (LAN or Wi-Fi): enter each area's printer address above, then run the Wise print bridge on any PC in the outlet. It's one small file; the setup steps are in print-bridge/README.</li>
            <li><strong>USB printer on a PC</strong>: leave the area's printer address empty and open the print station on that PC.</li>
          </ul>
          <table className="table">
            <thead><tr><th>Outlet</th><th>Print KOTs</th><th>Print station and bridge</th><th /></tr></thead>
            <tbody>
              {outlets.map((o) => (
                <tr key={o.id}>
                  <td>{o.name}</td>
                  <td><Toggle checked={!!o.print_kots} onChange={(v) => saveOutlet(o, { print_kots: v })} label={o.print_kots ? "On" : "Off"} /></td>
                  <td>
                    <div className="row">
                      <a href={printUrl(o)} target="_blank" rel="noreferrer">Open print station</a>
                      <button className="link" onClick={() => navigator.clipboard?.writeText(o.print_key || "")}>Copy print key</button>
                      <button className="link" onClick={() => navigator.clipboard?.writeText(
                        `python wise_print_bridge.py --server ${location.origin} --key ${o.print_key}`)}>Copy bridge command</button>
                    </div>
                  </td>
                  <td className="actions">
                    <button className="link danger" onClick={() => {
                      if (armed !== `p${o.id}`) { setArmed(`p${o.id}`); return; }
                      setArmed(null);
                      run(async () => { await api(`/tenant/outlets/${o.id}/print-key`, { method: "POST" }); await load(); });
                    }}>{armed === `p${o.id}` ? "Tap again: bridge needs the new key" : "New key"}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <ErrorText error={error} />
        </Card></Tab>
      )}

      {can("outlets.manage") && (
        <Tab label="QR and kiosk"><Card title="QR ordering and self-order kiosk">
          <p className="muted">Switch these on per outlet under Outlets. Print one QR per table; customers scan it, order, and the order waits under "Waiting for you" on the Orders screen until staff accept it. The kiosk link runs on a touch-screen monitor in full screen.</p>
          <table className="table">
            <thead><tr><th>Outlet</th><th>QR ordering</th><th>Kiosk</th><th /></tr></thead>
            <tbody>
              {outlets.map((o) => (
                <tr key={o.id}>
                  <td>{o.name}</td>
                  <td>{o.qr_ordering ? `On · ${(o.table_labels || []).length} tables${o.qr_auto_accept ? " · auto-accept" : ""}` : "Off"}</td>
                  <td>{o.kiosk_enabled ? <a href={kioskUrl(o)} target="_blank" rel="noreferrer">Open kiosk</a> : "Off"}</td>
                  <td className="actions">
                    {o.qr_ordering && <button className="link" onClick={() => setSheet(o)}>Print table QRs</button>}
                    {o.qr_ordering && <button className="link" onClick={() => navigator.clipboard?.writeText(qrUrl(o))}>Copy takeaway link</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card></Tab>
      )}

      {can("outlets.manage") && (
        <Tab label="Order-ready screen"><Card title="Customer order-ready screen">
          <p className="muted">Open this link on the TV or tablet facing customers. It shows token numbers under Preparing and Ready (with car numbers for takeaway). No login needed; anyone with the link can see token numbers only.</p>
          <table className="table">
            <thead><tr><th>Outlet</th><th>Display link</th><th /></tr></thead>
            <tbody>
              {outlets.map((o) => (
                <tr key={o.id}>
                  <td>{o.name}</td>
                  <td><a href={displayUrl(o)} target="_blank" rel="noreferrer" className="small">{displayUrl(o)}</a></td>
                  <td className="actions">
                    <button className="link" onClick={() => navigator.clipboard?.writeText(displayUrl(o))}>Copy</button>
                    <button className="link danger" onClick={() => {
                      if (armed !== `d${o.id}`) { setArmed(`d${o.id}`); return; }
                      setArmed(null);
                      run(async () => { await api(`/tenant/outlets/${o.id}/display-key`, { method: "POST" }); await load(); });
                    }}>{armed === `d${o.id}` ? "Tap again: old link stops" : "New link"}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card></Tab>
      )}
      </Tabs>
      {sheet && <QrSheet outlet={sheet} url={(t) => qrUrl(sheet, t)} onDone={() => setSheet(null)} />}
    </div>
  );
}

function QrSheet({ outlet, url, onDone }: { outlet: Outlet; url: (table?: string) => string; onDone: () => void }) {
  const tables = outlet.table_labels || [];
  return (
    <div className="modal-back" onClick={onDone}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="page-head">
          <h2>Table QR codes · {outlet.name}</h2>
          <div className="row"><Button onClick={() => window.print()}>Print</Button><Button variant="ghost" onClick={onDone}>Close</Button></div>
        </div>
        {tables.length === 0 && <p className="muted">Add tables to this outlet first (Outlets → Tables).</p>}
        {createPortal(
          <div id="print-root" className="qr-sheet">
            {[...tables, null].map((t) => (
              <div key={t || "takeaway"} className="qr-card">
                <QrImage text={url(t || undefined)} size={180} />
                <strong>{t ? `Table ${t}` : "Takeaway"}</strong>
                <span>Scan to see the menu and order</span>
              </div>
            ))}
          </div>, document.body)}
        <div className="qr-sheet preview">
          {[...tables, null].map((t) => (
            <div key={t || "takeaway"} className="qr-card"><QrImage text={url(t || undefined)} size={120} /><strong>{t ? `Table ${t}` : "Takeaway"}</strong></div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ago(iso?: string | null): string {
  if (!iso) return "";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
}

/** One line under each area: where its KOTs print and whether that's working. */
function PrinterStatus({ area, printing }: { area: PrepArea; printing: boolean }) {
  const where = area.printer_host ? `Network printer ${area.printer_host}` : "Print station (USB printer)";
  if (!printing) return <span className="pr-status muted">{where}</span>;
  const errorNewer = area.last_print_error && (!area.last_print_ok_at || (area.last_print_error_at || "") > area.last_print_ok_at);
  const stale = !area.station_seen_at || Date.now() - new Date(area.station_seen_at).getTime() > 2 * 60000;
  if (errorNewer) return <span className="pr-status bad">{where}: {area.last_print_error} ({ago(area.last_print_error_at)})</span>;
  if (stale) return <span className="pr-status warn">{where}: {area.printer_host ? "print bridge" : "print station"} not running{area.station_seen_at ? `, last seen ${ago(area.station_seen_at)}` : ""}</span>;
  return <span className="pr-status good">{where}: working{area.last_print_ok_at ? `, last KOT ${ago(area.last_print_ok_at)}` : ""}</span>;
}

function PrinterForm({ area, onSaved }: { area: PrepArea; onSaved: () => Promise<void> }) {
  const [host, setHost] = useState(area.printer_host || "");
  const [port, setPort] = useState(String(area.printer_port || 9100));
  const [paper, setPaper] = useState(String(area.paper_mm || 80));
  const [copies, setCopies] = useState(String(area.kot_copies || 1));
  const { busy, error, run } = useAction();
  return (
    <form className="printer-form" onSubmit={(e) => {
      e.preventDefault();
      run(async () => {
        await api(`/kitchen/prep-areas/${area.id}`, { method: "PATCH", body: {
          name: area.name, sort_order: area.sort_order, is_active: area.is_active, printer_host: host.trim() || null,
          printer_port: Number(port) || 9100, paper_mm: Number(paper), kot_copies: Number(copies) } });
        await onSaved();
      });
    }}>
      <label className="field"><span className="field-label">Printer address</span>
        <Input placeholder="e.g. 192.168.1.50, empty for USB" value={host} onChange={(e) => setHost(e.target.value)} />
      </label>
      <label className="field"><span className="field-label">Port</span>
        <Input inputMode="numeric" value={port} onChange={(e) => setPort(e.target.value)} disabled={!host.trim()} />
      </label>
      <label className="field"><span className="field-label">Paper</span>
        <Select value={paper} onChange={(e) => setPaper(e.target.value)}><option value="80">80 mm</option><option value="58">58 mm</option></Select>
      </label>
      <label className="field"><span className="field-label">Copies</span>
        <Select value={copies} onChange={(e) => setCopies(e.target.value)}>{[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}</Select>
      </label>
      <Button disabled={busy}>Save printer</Button>
      <ErrorText error={error} />
    </form>
  );
}
