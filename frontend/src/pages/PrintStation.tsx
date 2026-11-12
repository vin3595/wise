import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import { KotSlip } from "../components/KotSlip";
import type { PrintFeed, PrintJob } from "../types";

const store = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* storage blocked */ } },
};

function stationId(): string {
  let id = store.get("wise.print_station");
  if (!id) {
    id = `pc-${Math.random().toString(36).slice(2, 10)}`;
    store.set("wise.print_station", id);
  }
  return id;
}

type Done = { job: PrintJob; at: Date; error?: string };

/** A PC with a USB thermal printer prints the KOTs of its areas. Opened by the outlet's print link, no login.
 *  Chrome started with --kiosk-printing prints without the print dialog. */
export default function PrintStation({ printKey }: { printKey: string }) {
  const station = useRef(stationId()).current;
  const [feed, setFeed] = useState<PrintFeed | null>(null);
  const [badKey, setBadKey] = useState(false);
  const [offline, setOffline] = useState(false);
  const [running, setRunning] = useState(store.get("wise.print_running") === "1");
  const [only, setOnly] = useState<string[]>(() => (store.get("wise.print_areas") || "").split(",").filter(Boolean));
  const [printing, setPrinting] = useState<{ job: PrintJob; copies: number } | null>(null);
  const [log, setLog] = useState<Done[]>([]);

  useEffect(() => { store.set("wise.print_running", running ? "1" : "0"); }, [running]);
  useEffect(() => { store.set("wise.print_areas", only.join(",")); }, [only]);

  // Polls while running and prints jobs one after another. Not paused in the background: keep this tab open.
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const paint = () => new Promise<void>((r) => requestAnimationFrame(() => setTimeout(r, 150)));
    const tick = async () => {
      try {
        const q = new URLSearchParams({ station, mode: "browser", areas: only.join(","), peek: running ? "false" : "true" });
        const f = await api<PrintFeed>(`/public/print/${encodeURIComponent(printKey)}/jobs?${q}`);
        if (stop) return;
        setFeed(f);
        setOffline(false);
        document.title = `Print station · ${f.outlet_name}`;
        for (const job of running ? f.jobs : []) {
          if (stop) return;
          setPrinting({ job, copies: job.printer?.copies || 1 });
          await paint();
          let error: string | undefined;
          try { window.print(); } catch { error = "The browser blocked printing"; }
          setPrinting(null);
          await api(`/public/print/${encodeURIComponent(printKey)}/jobs/${job.kot_id}?station=${station}`,
            { body: { kind: job.kind, ok: !error, error: error || null } });
          setLog((l) => [{ job, at: new Date(), error }, ...l].slice(0, 12));
        }
      } catch (e) {
        if (stop) return;
        if (e instanceof Error && /not found/i.test(e.message)) setBadKey(true);
        else setOffline(true);
      }
      if (!stop) timer = setTimeout(tick, 2500);
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [printKey, running, only.join(","), station]);

  if (badKey) return <div className="splash">This print link isn't valid any more. Copy the new one from Kitchen setup &gt; Printing.</div>;
  if (!feed) return <div className="splash">Connecting…</div>;
  const toggle = (id: string) => setOnly((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  return (
    <div className="ps">
      <header className="ps-head">
        <div className="grow">
          <h1>Print station</h1>
          <p className="muted">{feed.app_name}, {feed.outlet_name}. KOTs print on this computer's printer.</p>
        </div>
        <button className={`ps-power ${running ? "on" : ""}`} onClick={() => setRunning(!running)} aria-pressed={running}>
          <span className="ps-dot" />{running ? "Printing" : "Start printing"}
        </button>
      </header>

      {!feed.enabled && <p className="banner warn">KOT printing is off for this outlet. Switch on "Print KOTs" under Kitchen setup.</p>}
      {offline && <p className="banner warn">Can't reach Wise. KOTs will print as soon as the connection is back.</p>}

      <section className="ps-areas">
        <h2>Areas printed here</h2>
        {feed.areas.length === 0 ? (
          <p className="muted">Every area has a network printer address, so the print bridge prints them all. Nothing to print here.</p>
        ) : (
          <div className="chips">
            <button className={`choice ${only.length === 0 ? "on" : ""}`} onClick={() => setOnly([])}>All areas without a network printer</button>
            {feed.areas.map((a) => (
              <button key={a.id} className={`choice ${only.includes(a.id) ? "on" : ""}`} onClick={() => toggle(a.id)}>{a.name}</button>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2>Printed here</h2>
        {log.length === 0 && <p className="muted">{running ? "Waiting for KOTs. Keep this tab open." : "Printing is paused. Tap Start printing."}</p>}
        <div className="ps-roll">
          {log.map((d) => (
            <figure key={`${d.job.kot_id}-${d.at.getTime()}`} className={d.error ? "failed" : ""}>
              <KotSlip job={d.job} />
              <figcaption>{d.error ? d.error : `Printed ${d.at.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", second: "2-digit" })}`}</figcaption>
            </figure>
          ))}
        </div>
      </section>

      <details className="ps-help">
        <summary>Print without the print dialog</summary>
        <p>Set the thermal printer as this computer's default printer, then start Chrome with the
          <code> --kiosk-printing </code> option (add it to the Chrome shortcut's Target, after chrome.exe). Each KOT then
          prints straight away. Set paper size to 80 mm (or 58 mm) and margins to none in the printer settings.</p>
      </details>

      {printing && createPortal(
        <div id="print-root">
          {Array.from({ length: printing.copies }, (_, i) => (
            <KotSlip key={i} job={printing.job} copy={i + 1} copies={printing.copies} paper={printing.job.printer?.paper_mm || 80} />
          ))}
        </div>, document.body)}
    </div>
  );
}
