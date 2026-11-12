import { useEffect, useState } from "react";
import { api } from "../api";
import { formatPaise, inputToPaise, paiseToInput } from "../money";
import { Button, Card, ErrorText, Field, Input, Select, useAction, Tab, Tabs } from "../ui";

interface Rules { mode: "off" | "points" | "stamps" | "both"; earn_bp: number; min_bill_paise: number; redeem_min_paise: number; max_redeem_bp: number; expiry_days: number;
  stamp_min_paise: number; stamps_needed: number; reward_paise: number; reward_label: string }
interface Cust { id: string; phone: string; name: string | null; visits: number; spent_paise: number; avg_paise: number; points_paise: number; stamps: number; first_visit: string | null; last_visit: string | null }
interface Entry { at: string; kind: string; points_paise: number; stamps: number; bill: string | null; note: string | null; by: string | null }

const MODES: [Rules["mode"], string, string][] = [
  ["off", "Off", "No points or stamps. Customers are still listed below."],
  ["points", "Points", "A share of every bill comes back as points worth rupees, used on a later bill."],
  ["stamps", "Visit stamps", "One stamp per bill over a minimum; after enough stamps the next bill gets a reward."],
  ["both", "Both", "Points and stamps together."],
];
const KIND: Record<string, string> = { earn: "Earned", redeem: "Used", reward: "Reward", expire: "Lapsed", adjust: "Adjusted" };
const pct = (bp: number) => String(bp / 100);

/** Loyalty rules and the customer list. */
export default function Customers() {
  const [r, setR] = useState<Rules | null>(null);
  const [f, setF] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);
  const [list, setList] = useState<Cust[]>([]);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("spent");
  const [open, setOpen] = useState<Cust | null>(null);
  const { busy, error, run } = useAction();

  const fill = (x: Rules) => { setR(x); setF({ earn: pct(x.earn_bp), min: paiseToInput(x.min_bill_paise), rmin: paiseToInput(x.redeem_min_paise), rmax: pct(x.max_redeem_bp), exp: String(x.expiry_days),
    smin: paiseToInput(x.stamp_min_paise), need: String(x.stamps_needed), rew: paiseToInput(x.reward_paise), label: x.reward_label }); };
  useEffect(() => { api<Rules>("/tenant/loyalty").then(fill); }, []);
  useEffect(() => { const t = setTimeout(() => api<Cust[]>(`/loyalty/customers?sort=${sort}${q ? `&q=${encodeURIComponent(q)}` : ""}`).then(setList), 200); return () => clearTimeout(t); }, [q, sort, saved]);
  if (!r) return <p className="muted">Loading…</p>;
  const set = (k: string, v: string) => { setF({ ...f, [k]: v }); setSaved(false); };
  const showP = r.mode === "points" || r.mode === "both", showS = r.mode === "stamps" || r.mode === "both";
  const save = () => run(async () => {
    const num = (v: string) => Number(v || "0");
    fill(await api<Rules>("/tenant/loyalty", { method: "PUT", body: { mode: r.mode, earn_bp: Math.round(num(f.earn) * 100), min_bill_paise: inputToPaise(f.min || "0") ?? 0,
      redeem_min_paise: inputToPaise(f.rmin || "0") ?? 0, max_redeem_bp: Math.round(num(f.rmax) * 100), expiry_days: Math.round(num(f.exp)),
      stamp_min_paise: inputToPaise(f.smin || "0") ?? 0, stamps_needed: Math.max(1, Math.round(num(f.need))), reward_paise: inputToPaise(f.rew || "0") ?? 0, reward_label: f.label || "Reward" } }));
    setSaved(true);
  });
  const example = showP ? `A ₹200 bill earns ₹${(200 * Number(f.earn || 0)) / 100} of points.` : "";
  return (
    <div className="stack">
      <h1>Customers</h1>
      <Tabs id="customers">
      <Tab label="Customers">
      <Card title={`Customers (${list.length})`}>
        <div className="row">
          <Input style={{ flex: 1 }} placeholder="Find by mobile or name" value={q} onChange={(e) => setQ(e.target.value)} />
          <Select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort customers">
            <option value="spent">Spent most</option><option value="visits">Most visits</option><option value="recent">Visited recently</option>
            <option value="lapsing">Not seen for longest</option><option value="points">Most points</option>
          </Select>
        </div>
        <table className="table">
          <thead><tr><th>Customer</th><th className="num">Visits</th><th className="num">Spent</th><th className="num">Average</th>{showP && <th className="num">Points</th>}{showS && <th className="num">Stamps</th>}<th>Last visit</th></tr></thead>
          <tbody>
            {list.map((c) => (
              <tr key={c.id} onClick={() => setOpen(c)} className="clickable">
                <td>{c.name || "–"}<div className="muted small">{c.phone}</div></td>
                <td className="num">{c.visits}</td><td className="num">{formatPaise(c.spent_paise)}</td><td className="num">{formatPaise(c.avg_paise)}</td>
                {showP && <td className="num">{formatPaise(c.points_paise)}</td>}{showS && <td className="num">{c.stamps}</td>}
                <td className="small">{c.last_visit || "–"}</td>
              </tr>
            ))}
            {list.length === 0 && <tr><td colSpan={7} className="muted">Customers appear here once they give their mobile number on a bill.</td></tr>}
          </tbody>
        </table>
      </Card>
      </Tab>
      <Tab label="Loyalty rules">
      <Card title="Loyalty">
        <div className="mode-pick">
          {MODES.map(([m, label, help]) => (
            <button key={m} className={`mode ${r.mode === m ? "on" : ""}`} onClick={() => { setR({ ...r, mode: m }); setSaved(false); }} aria-pressed={r.mode === m}>
              <strong>{label}</strong><span>{help}</span>
            </button>
          ))}
        </div>
        {showP && <>
          <h3>Points</h3>
          <div className="grid2">
            <Field label="Back as points (%)" hint={example}><Input inputMode="decimal" value={f.earn} onChange={(e) => set("earn", e.target.value)} /></Field>
            <Field label="Smallest bill that earns (₹)"><Input inputMode="decimal" value={f.min} onChange={(e) => set("min", e.target.value)} /></Field>
            <Field label="Points usable from (₹)"><Input inputMode="decimal" value={f.rmin} onChange={(e) => set("rmin", e.target.value)} /></Field>
            <Field label="Points pay at most (% of a bill)"><Input inputMode="decimal" value={f.rmax} onChange={(e) => set("rmax", e.target.value)} /></Field>
            <Field label="Points lapse after (days without a visit)" hint="0 = never"><Input inputMode="numeric" value={f.exp} onChange={(e) => set("exp", e.target.value)} /></Field>
          </div>
        </>}
        {showS && <>
          <h3>Visit stamps</h3>
          <div className="grid2">
            <Field label="A bill of at least (₹) earns a stamp"><Input inputMode="decimal" value={f.smin} onChange={(e) => set("smin", e.target.value)} /></Field>
            <Field label="Stamps for the reward"><Input inputMode="numeric" value={f.need} onChange={(e) => set("need", e.target.value)} /></Field>
            <Field label="Reward: up to (₹) off"><Input inputMode="decimal" value={f.rew} onChange={(e) => set("rew", e.target.value)} /></Field>
            <Field label="Reward name (shown to the cashier)"><Input value={f.label} onChange={(e) => set("label", e.target.value)} /></Field>
          </div>
        </>}
        <p className="muted small">The cashier sees the customer's points and stamps once the mobile number is typed, and uses them with one tap. Used points and rewards count as a discount on the bill (GST is on the amount after it) and don't need the manager's PIN. Cancelling a bill undoes what it earned and used.</p>
        <ErrorText error={error} />
        {saved && <p className="ok">Saved.</p>}
        <Button disabled={busy} onClick={save}>Save</Button>
      </Card>
      </Tab>
      </Tabs>
      {open && <CustomerPanel c={open} onClose={() => { setOpen(null); setSaved((x) => !x); }} />}
    </div>
  );
}

function CustomerPanel({ c, onClose }: { c: Cust; onClose: () => void }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [pts, setPts] = useState(""), [st, setSt] = useState(""), [note, setNote] = useState("");
  const [cur, setCur] = useState({ points_paise: c.points_paise, stamps: c.stamps });
  const { busy, error, run } = useAction();
  const load = () => api<Entry[]>(`/loyalty/customers/${c.id}/entries`).then(setEntries);
  useEffect(() => { load(); }, [c.id]);
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="page-head"><h2>{c.name || c.phone}</h2><button className="link" onClick={onClose}>Close</button></div>
        <p>{c.phone} · {c.visits} visits · spent {formatPaise(c.spent_paise)} · since {c.first_visit || "–"}</p>
        <p><strong>Points {formatPaise(cur.points_paise)}</strong> · <strong>{cur.stamps} stamps</strong></p>
        <table className="table"><tbody>
          {entries.map((e, i) => <tr key={i}><td className="small">{new Date(e.at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}</td><td>{KIND[e.kind] || e.kind}{e.bill && <span className="muted small"> · {e.bill}</span>}{e.note && e.note !== e.bill && <div className="muted small">{e.note}</div>}</td>
            <td className="num">{e.points_paise ? `${e.points_paise > 0 ? "+" : "−"}${formatPaise(Math.abs(e.points_paise))}` : ""}</td><td className="num">{e.stamps ? `${e.stamps > 0 ? "+" : ""}${e.stamps} stamp${Math.abs(e.stamps) === 1 ? "" : "s"}` : ""}</td></tr>)}
          {entries.length === 0 && <tr><td className="muted">No points or stamps yet.</td></tr>}
        </tbody></table>
        <h3>Adjust</h3>
        <div className="row">
          <Input inputMode="decimal" placeholder="Points ₹ (e.g. 50 or -20)" value={pts} onChange={(e) => setPts(e.target.value)} />
          <Input inputMode="numeric" placeholder="Stamps (+/-)" value={st} onChange={(e) => setSt(e.target.value)} />
        </div>
        <Input placeholder="Why (e.g. complaint, goodwill)" value={note} onChange={(e) => setNote(e.target.value)} />
        <ErrorText error={error} />
        <Button disabled={busy || note.trim().length < 3 || (!pts && !st)} onClick={() => run(async () => {
          const neg = pts.trim().startsWith("-"), p = inputToPaise(pts.replace("-", "") || "0") ?? 0;
          setCur(await api<typeof cur>(`/loyalty/customers/${c.id}/adjust`, { body: { points_paise: neg ? -p : p, stamps: Math.round(Number(st || 0)), note } }));
          setPts(""); setSt(""); setNote(""); load();
        })}>Save adjustment</Button>
      </div>
    </div>
  );
}
