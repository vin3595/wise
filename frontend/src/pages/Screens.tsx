import { useEffect, useState } from "react";
import { api } from "../api";
import { Button, Card, ErrorText, Field, Select, useAction, Tab, Tabs } from "../ui";
import type { Outlet, PrepArea } from "../types";
import InstallApp from "../components/InstallApp";

/** One-time setup of every screen at an outlet: each gets a fixed link and a start-up file,
 *  so when the shop opens and the power comes on, every screen comes up by itself. */
type Kind = "display" | "kitchen" | "kiosk" | "print" | "counter";

const CHROME_FLAGS = "--autoplay-policy=no-user-gesture-required --noerrdialogs --disable-session-crashed-bubble --disable-features=Translate --no-first-run";

export function startupFile(o: { name: string; screen: string; slug: string; url?: string; origin: string; mainWidth?: number; dual?: boolean; print?: boolean }): string {
  const head = [
    "@echo off",
    `rem Wise: ${o.screen} at ${o.name}.`,
    "rem Put this file in the Startup folder (press Win+R, type shell:startup, Enter) so it starts when the PC turns on.",
    "rem Waits for the network and the Wise Box first.",
    "timeout /t 20 /nobreak >nul",
  ];
  const profile = `--user-data-dir="%LOCALAPPDATA%\\Wise\\${o.slug}"`;
  const body = o.dual
    ? [
        "rem 1) Customer screen, full screen on the second monitor (to the right of the main one).",
        `start "" chrome ${profile} --app="${o.origin}/customer-screen?kiosk=1" --window-position=${o.mainWidth || 1920},0 --start-fullscreen ${CHROME_FLAGS}`,
        "timeout /t 5 /nobreak >nul",
        "rem 2) Billing counter on the main monitor. The cashier signs in with their PIN.",
        "rem    --kiosk-printing: bills print straight to the default printer, no print dialog.",
        `start "" chrome ${profile} --app="${o.origin}/orders" --start-maximized --kiosk-printing ${CHROME_FLAGS}`,
      ]
    : [`start "" chrome ${profile} --kiosk "${o.url}"${o.print ? " --kiosk-printing" : ""} ${CHROME_FLAGS}`];
  return [...head, ...body, ""].join("\r\n");
}

function download(name: string, text: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "application/octet-stream" }));
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export default function Screens() {
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [areas, setAreas] = useState<PrepArea[]>([]);
  const [outletId, setOutletId] = useState("");
  const [kitchenArea, setKitchenArea] = useState("");
  const [mainWidth, setMainWidth] = useState("1920");
  const [copied, setCopied] = useState<string | null>(null);
  const act = useAction();
  useEffect(() => {
    act.run(async () => {
      const o = await api<Outlet[]>("/tenant/outlets");
      setOutlets(o.filter((x) => x.is_active));
      setOutletId((cur) => cur || o[0]?.id || "");
      setAreas((await api<PrepArea[]>("/kitchen/prep-areas")).filter((a) => a.is_active));
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const o = outlets.find((x) => x.id === outletId);
  const origin = location.origin;
  if (!o) return <div className="stack"><h1>Screens</h1><ErrorText error={act.error} /></div>;

  const slugOf = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "screen";
  const kitchenUrl = `${origin}/kitchen/${o.kitchen_key}?kiosk=1${kitchenArea ? `&area=${kitchenArea}` : ""}`;
  const areaName = kitchenArea === "__kitchen" ? "No prep area" : areas.find((a) => a.id === kitchenArea)?.name;
  const rows: { kind: Kind; tab: string; title: string; what: string; url?: string; show: boolean }[] = [
    { kind: "counter", tab: "Billing counter", title: "Billing counter (with customer screen)", what: "Main monitor: Orders, the cashier signs in with their PIN. Bills print straight to the bill printer, no print dialog (make it Windows' default printer). Second monitor: the customer screen with your ads, full screen, nobody can touch it unless the cashier asks for their number.", show: true },
    { kind: "kitchen", tab: "Kitchen", title: `Kitchen display${areaName ? `: ${areaName}` : ""}`, what: "Signs itself in. No chef PIN, no daily login. Sound on by itself.", url: kitchenUrl, show: true },
    { kind: "display", tab: "Order status TV", title: "Order status TV", what: "Token numbers: being prepared / ready to collect. Chime on by itself.", url: `${origin}/display/${o.display_key}?kiosk=1`, show: true },
    { kind: "kiosk", tab: "Kiosk", title: "Self-order kiosk", what: "Customers order and pay on the touch screen. Resets itself after 90 seconds idle.", url: `${origin}/kiosk/${o.qr_key}?kiosk=1`, show: o.kiosk_enabled },
    { kind: "print", tab: "Print station", title: "KOT print station", what: "The PC with the USB/LAN kitchen printer. Prints by itself.", url: `${origin}/print/${o.print_key}?kiosk=1`, show: o.print_kots },
  ];

  return (
    <div className="stack">
      <h1>Screens</h1>
      <p className="muted">Set each screen up once. After that, when the shop opens and the power comes on, every screen starts by itself on the right page, full screen, with sound. Nobody has to log in or tap anything, except the cashier signing in with their PIN.</p>
      <div className="grid2" style={{ maxWidth: "40rem" }}>
        <Field label="Outlet"><Select value={outletId} onChange={(e) => setOutletId(e.target.value)}>{outlets.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
      </div>
      <ErrorText error={act.error} />

      <Tabs id="screens">
      {rows.filter((r) => r.show).map((r) => (
        <Tab key={r.kind} label={r.tab}><Card title={r.title}>
          <p className="muted small">{r.what}</p>
          {r.kind === "kitchen" && areas.length > 0 && (
            <div className="grid2" style={{ maxWidth: "40rem" }}>
              <Field label="Which station is this screen for?" hint="One link per screen: each comes up on its own section.">
                <Select value={kitchenArea} onChange={(e) => setKitchenArea(e.target.value)}>
                  <option value="">All areas</option>
                  {areas.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </Select>
              </Field>
            </div>
          )}
          {r.kind === "counter" && (
            <div className="grid2" style={{ maxWidth: "40rem" }}>
              <Field label="Main monitor width" hint="Where the second monitor starts. Windows: Settings > Display shows it.">
                <Select value={mainWidth} onChange={(e) => setMainWidth(e.target.value)}>
                  {["1024", "1280", "1366", "1440", "1600", "1920"].map((w) => <option key={w} value={w}>{w} pixels</option>)}
                </Select>
              </Field>
            </div>
          )}
          {r.url && <div className="screen-link"><code>{r.url}</code>
            <button className="link" onClick={() => { navigator.clipboard?.writeText(r.url!).then(() => { setCopied(r.kind); setTimeout(() => setCopied(null), 2000); }).catch(() => undefined); }}>{copied === r.kind ? "Copied ✓" : "Copy link"}</button>
          </div>}
          <div className="row wrap">
            <Button variant="ghost" onClick={() => download(`Wise ${r.title.replace(/[^A-Za-z0-9 ]+/g, "").trim()} - ${o.name}.bat`.replace(/\s+/g, " "),
              startupFile({ name: o.name, screen: r.title, slug: r.kind === "counter" ? `counter-${slugOf(o.code)}` : `${r.kind}-${slugOf(o.code)}${r.kind === "kitchen" && kitchenArea ? "-" + kitchenArea.slice(0, 6) : ""}`,
                url: r.url, origin, mainWidth: Number(mainWidth), dual: r.kind === "counter", print: r.kind === "print" || r.kind === "kiosk" }))}>
              Download start-up file (Windows PC)
            </Button>
            {r.kind === "kitchen" && <button className="link" onClick={() => {
              if (confirm("Make a new kitchen link? Screens on the old link stop and need the new start-up file.")) act.run(async () => {
                const n = await api<Outlet>(`/tenant/outlets/${o.id}/kitchen-key`, { method: "POST" });
                setOutlets((l) => l.map((x) => (x.id === n.id ? n : x)));
              });
            }}>New kitchen link</button>}
          </div>
        </Card></Tab>
      ))}

      <Tab label="Install the app"><Card title="Install Wise as an app on this device">
        <p className="muted small">The installed app opens from its own icon, full screen, without the browser's address bar. It updates itself whenever Wise is updated.</p>
        <InstallApp full />
        <ul className="steps">
          <li><strong>Android phone or tablet:</strong> open Wise in Chrome, tap ⋮ and then <strong>Install app</strong> (or "Add to Home screen").</li>
          <li><strong>Windows PC:</strong> open Wise in Chrome or Edge and click the install icon at the right end of the address bar, or ⋮ &gt; <strong>Install Wise</strong>.</li>
          <li><strong>iPhone or iPad:</strong> open Wise in Safari, tap Share and then <strong>Add to Home Screen</strong>.</li>
        </ul>
      </Card></Tab>

      <Tab label="How to set up"><Card title="How to set up a screen (once)">
        <ol className="steps">
          <li><strong>Windows PC or mini-PC:</strong> download its start-up file above, press Win+R, type <code>shell:startup</code>, press Enter and drop the file in that folder. Restart once to check. Set the PC to log in to Windows by itself and to never sleep.</li>
          <li><strong>Android TV box, Fire TV stick or tablet:</strong> install a kiosk browser app that has "start on boot" (for example Fully Kiosk Browser), paste the screen's link as its start page and turn on start on boot.</li>
          <li><strong>The TV itself:</strong> in its settings, turn on "power on with last input" (or "auto power on"), so after a power cut it comes back on the same HDMI input.</li>
          <li><strong>Touch on the dual-screen counter:</strong> once, in Windows Control Panel &gt; Tablet PC Settings &gt; Setup, tap the customer screen when asked, so touches land on the right monitor.</li>
        </ol>
        <p className="muted small">Each link only opens that one screen for this outlet. If a link gets out, make a new one: kitchen links here, the TV link under Outlets.</p>
      </Card></Tab>
      </Tabs>
    </div>
  );
}
