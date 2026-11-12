import { useRef, useState } from "react";
import { api } from "../api";
import { chime, unlockAudio, usePolling, startedByScreenFile } from "../hooks";
import type { Display } from "../types";

/** TV at the counter: "Preparing" and "Ready" token numbers. Opened by a secret link, no login. */
export default function CustomerDisplay({ displayKey }: { displayKey: string }) {
  const [data, setData] = useState<Display | null>(null);
  const [sound, setSound] = useState(startedByScreenFile);
  const [fresh, setFresh] = useState<Set<number>>(new Set());
  const readySeen = useRef<Set<number> | null>(null);

  const error = usePolling(async () => {
    const d = await api<Display>(`/public/display/${encodeURIComponent(displayKey)}`);
    document.documentElement.style.setProperty("--brand", d.primary_color);
    document.title = `${d.app_name} · Orders`;
    const ready = new Set(d.ready.map((o) => o.token_no));
    if (readySeen.current) {
      const newly = [...ready].filter((t) => !readySeen.current!.has(t));
      if (newly.length) {
        if (sound) chime();
        setFresh(new Set(newly));
        setTimeout(() => setFresh(new Set()), 6000);
      }
    }
    readySeen.current = ready;
    setData(d);
  }, 3000, [displayKey, sound], { displayKey, topics: ["orders"] });

  if (!data) {
    return <div className="splash">{error ? "This display link isn't valid. Ask the manager for the outlet's display link." : "Loading…"}</div>;
  }
  const time = new Date().toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
  return (
    <div className="tv" onClick={() => { unlockAudio(); setSound(true); }}>
      <header className="tv-head">
        {data.logo_url ? <img src={data.logo_url} alt="" /> : <span className="tv-mark">{data.app_name[0]}</span>}
        <div className="tv-name"><strong>{data.app_name}</strong><span>{data.outlet_name}</span></div>
        <span className="tv-clock">{time}</span>
      </header>
      {(!sound || error) && <p className="tv-hint">{error ? "Reconnecting…" : "Tap the screen once to turn on the chime"}</p>}
      <main className="tv-body">
        <section className="tv-ready" aria-live="polite">
          <h2>Ready to collect</h2>
          <div className="tv-ready-grid">
            {data.ready.map((o) => (
              <div key={o.token_no} className={`tv-token ${fresh.has(o.token_no) ? "fresh" : ""}`}>
                <span className="n">{o.token_no}</span>
                {o.car_number && <span className="d">Car {o.car_number}</span>}
                {o.table_label && <span className="d">Table {o.table_label}</span>}
              </div>
            ))}
            {data.ready.length === 0 && <p className="tv-empty">Your token appears here when it's ready.</p>}
          </div>
        </section>
        <section className="tv-prep">
          <h2>Being prepared</h2>
          <div className="tv-prep-grid">
            {data.preparing.map((o) => <span key={o.token_no} className="tv-small">{o.token_no}</span>)}
            {data.preparing.length === 0 && <p className="tv-empty">No orders waiting.</p>}
          </div>
        </section>
      </main>
      {data.show_powered_by && <footer className="tv-foot">Powered by Wise</footer>}
    </div>
  );
}
