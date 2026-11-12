import SoldOutPanel from "../components/SoldOutPanel";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { ORDER_TYPE_LABEL, ageLevel, chime, elapsed, unlockAudio, useNow, usePolling, startedByScreenFile } from "../hooks";
import { useSession } from "../session";
import type { Kot, KotStatus, Outlet, PrepArea } from "../types";
import { FoodDot, Select } from "../ui";

const COLUMNS: { status: KotStatus; title: string }[] = [
  { status: "new", title: "New" },
  { status: "preparing", title: "Preparing" },
  { status: "ready", title: "Ready" },
];

const UNASSIGNED = "__kitchen";

/** Kitchen display: one screen per preparation area, tickets move New -> Preparing -> Ready -> Served. */
export default function Kds() {
  const { me, can, deviceOutletId } = useSession();
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [outletId, setOutletId] = useState<string>(me?.outlet_id || deviceOutletId || "");
  const [areas, setAreas] = useState<PrepArea[]>([]);
  // A kitchen link can name its station (?area=<id>), so each screen comes up on its own section.
  const [area, setArea] = useState<string>(() => {
    const fromLink = new URLSearchParams(location.search).get("area");
    if (fromLink !== null) { try { localStorage.setItem("wise.kds_area", fromLink); } catch { /* */ } return fromLink; }
    return localStorage.getItem("wise.kds_area") || "";
  });
  const [kots, setKots] = useState<Kot[]>([]);
  const [sound, setSound] = useState(startedByScreenFile);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const seen = useRef<Set<string> | null>(null);
  const skew = useRef(0);
  const now = useNow() + skew.current;

  useEffect(() => {
    document.body.classList.add("screen-dark");
    return () => document.body.classList.remove("screen-dark");
  }, []);

  useEffect(() => {
    api<Outlet[]>("/tenant/outlets").then((o) => {
      setOutlets(o);
      setOutletId((cur) => cur || o[0]?.id || "");
    });
    api<PrepArea[]>("/kitchen/prep-areas").then((a) => setAreas(a.filter((x) => x.is_active)));
  }, []);

  const query = area === UNASSIGNED ? "?unassigned=true" : area ? `?prep_area_id=${area}` : "";
  const pollError = usePolling(async () => {
    if (!outletId) return;
    const res = await api<{ server_time: string; kots: Kot[] }>(`/kds/outlets/${outletId}${query}`);
    skew.current = new Date(res.server_time).getTime() - Date.now();
    const ids = new Set(res.kots.map((k) => k.id));
    if (seen.current && sound && res.kots.some((k) => k.status === "new" && !seen.current!.has(k.id))) chime();
    seen.current = ids;
    setKots(res.kots);
  }, 4000, [outletId, query, sound], { outletId, topics: ["orders"] });

  const byStatus = useMemo(() => {
    const m: Record<string, Kot[]> = { new: [], preparing: [], ready: [] };
    kots.forEach((k) => m[k.status]?.push(k));
    m.ready.sort((a, b) => (b.ready_at || "").localeCompare(a.ready_at || ""));
    return m;
  }, [kots]);

  async function move(k: Kot, status: KotStatus) {
    setBusyId(k.id);
    setActionError(null);
    try {
      const updated = await api<Kot>(`/kds/kots/${k.id}/status`, { body: { status } });
      setKots((list) => (status === "served" ? list.filter((x) => x.id !== k.id) : list.map((x) => (x.id === k.id ? updated : x))));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Couldn't update the ticket");
    } finally {
      setBusyId(null);
    }
  }

  const canKitchen = can("kds.update");
  const [soldOpen, setSoldOpen] = useState(false);
  const canServe = can("order.complete");
  const areaName = area === UNASSIGNED ? "No prep area" : areas.find((a) => a.id === area)?.name || "All areas";

  return (
    <div className="kds" onClick={() => sound && unlockAudio()}>
      <div className="kds-bar">
        <strong className="kds-title">{areaName}</strong>
        {!me?.outlet_id && outlets.length > 1 && (
          <Select value={outletId} onChange={(e) => setOutletId(e.target.value)}>
            {outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </Select>
        )}
        <div className="kds-areas">
          {[{ id: "", name: "All" }, ...areas, { id: UNASSIGNED, name: "No prep area" }].map((a) => (
            <button key={a.id} className={area === a.id ? "active" : ""}
              onClick={() => { setArea(a.id); localStorage.setItem("wise.kds_area", a.id); seen.current = null; }}>
              {a.name}
            </button>
          ))}
        </div>
        <div className="row">
          <button className={`btn ${sound ? "btn-primary" : "btn-ghost"}`} onClick={() => { unlockAudio(); setSound(!sound); }}>
            {sound ? "Sound on" : "Sound off"}
          </button>
          {(canKitchen) && outletId && <button className="btn btn-ghost" onClick={() => setSoldOpen(true)}>Sold out</button>}
          <button className="btn btn-ghost" onClick={() => document.documentElement.requestFullscreen?.()}>Full screen</button>
        </div>
      </div>
      {soldOpen && outletId && <SoldOutPanel outletId={outletId} onClose={() => setSoldOpen(false)} />}
      {(pollError || actionError) && <p className="error kds-error">{actionError || `Not updating: ${pollError}`}</p>}
      <div className="kds-cols">
        {COLUMNS.map((col) => (
          <section key={col.status} className={`kds-col kds-col-${col.status}`}>
            <h2>{col.title} <span className="count">{byStatus[col.status].length}</span></h2>
            <div className="kds-tickets">
              {byStatus[col.status].map((k) => (
                <Ticket key={k.id} k={k} now={now} busy={busyId === k.id} canKitchen={canKitchen} canServe={canServe} onMove={move}
                  showArea={!area} />
              ))}
              {byStatus[col.status].length === 0 && <p className="muted center">Nothing here</p>}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function Ticket({ k, now, busy, canKitchen, canServe, onMove, showArea }: {
  k: Kot; now: number; busy: boolean; canKitchen: boolean; canServe: boolean; showArea: boolean;
  onMove: (k: Kot, s: KotStatus) => void;
}) {
  const level = k.status === "ready" ? "ok" : ageLevel(k.created_at, now);
  const where = k.order_type === "dine_in" ? `Table ${k.table_label}` : k.order_type === "takeaway" ? (k.car_number ? `Car ${k.car_number}` : "Takeaway") : "Delivery";
  // Tap anywhere on the ticket for the next step; Undo steps back.
  const next: { to: KotStatus; label: string } | null =
    k.status === "new" && canKitchen ? { to: "preparing", label: "Tap to start" }
      : k.status === "preparing" && canKitchen ? { to: "ready", label: "Tap when ready" }
        : k.status === "ready" && canServe ? { to: "served", label: k.order_type === "dine_in" ? "Tap when served" : "Tap when handed over" }
          : null;
  const back: KotStatus | null = canKitchen ? (k.status === "preparing" ? "new" : k.status === "ready" ? "preparing" : null) : null;
  return (
    <article className={`ticket st-${k.status} age-${level} ${next ? "tappable" : ""}`} aria-busy={busy}
      role={next ? "button" : undefined} tabIndex={next ? 0 : undefined}
      onClick={() => next && !busy && onMove(k, next.to)}
      onKeyDown={(e) => { if (next && !busy && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onMove(k, next.to); } }}>
      <header>
        <span className="t-token">{k.token_no}</span>
        <div className="t-where">
          <strong>{where}</strong>
          {(where !== ORDER_TYPE_LABEL[k.order_type] || k.customer_name) && (
            <span>{[where !== ORDER_TYPE_LABEL[k.order_type] ? ORDER_TYPE_LABEL[k.order_type] : null, k.customer_name].filter(Boolean).join(", ")}</span>
          )}
        </div>
        <span className="t-timer">{k.status === "ready" && k.ready_at ? elapsed(k.ready_at, now) : elapsed(k.created_at, now)}</span>
      </header>
      <div className="t-meta">
        KOT {k.kot_no}{showArea ? ` for ${k.prep_area_name}` : ""}
        {k.is_addon_kot && <span className="badge addon">Added items</span>}
      </div>
      <ul className="t-lines">
        {k.lines.map((l) => (
          <li key={l.id}>
            <span className="t-qty">{l.qty}</span>
            <span>
              <FoodDot type={l.food_type} /> {l.item_name}{l.variant_name !== "Regular" ? <em> {l.variant_name}</em> : null}
              {l.addons.length > 0 && <span className="t-sub">+ {l.addons.map((a) => a.name).join(", ")}</span>}
              {l.notes && <span className="t-sub t-note">{l.notes}</span>}
            </span>
          </li>
        ))}
      </ul>
      {k.order_notes && <p className="t-ordernote">{k.order_notes}</p>}
      <footer>
        <span className="t-next">{busy ? "Updating…" : next?.label}</span>
        {back && <button className="t-undo" disabled={busy} onClick={(e) => { e.stopPropagation(); onMove(k, back); }}>Undo</button>}
        {k.status === "new" && canKitchen && (
          <button className="t-undo" disabled={busy} onClick={(e) => { e.stopPropagation(); onMove(k, "ready"); }}>Ready now</button>
        )}
        {k.printed && <Reprint kotId={k.id} />}
      </footer>
    </article>
  );
}

/** Asks the printer for this KOT again; it prints marked "Reprint". */
function Reprint({ kotId }: { kotId: string }) {
  const [state, setState] = useState<"idle" | "sent" | "error">("idle");
  return (
    <button className="t-undo" disabled={state === "sent"} onClick={async (e) => {
      e.stopPropagation();
      try { await api(`/orders/kots/${kotId}/reprint`, { method: "POST" }); setState("sent"); setTimeout(() => setState("idle"), 4000); }
      catch { setState("error"); }
    }}>{state === "sent" ? "Reprinting" : state === "error" ? "Reprint failed" : "Reprint"}</button>
  );
}
