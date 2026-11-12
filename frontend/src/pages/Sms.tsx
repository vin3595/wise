import { useEffect, useState } from "react";
import { api } from "../api";
import { Button, Card, ErrorText, Input, Select, Toggle, useAction, Tab, Tabs } from "../ui";

interface Settings {
  enabled: boolean; provider: "msg91" | "log"; has_auth_key: boolean; sender_id: string | null;
  template_ids: Record<string, string>; send_bill: boolean; send_ready: boolean; send_daily: boolean; send_cash: boolean; send_alerts: boolean; alert_discount_bp: number; daily_to: string[];
  templates: Record<string, { label: string; text: string }>;
}
interface Msg { id: string; kind: string; to_phone: string; body: string; status: "queued" | "sent" | "failed"; attempts: number; last_error: string | null; created_at: string; sent_at: string | null }

const KIND: Record<string, string> = { bill: "E-bill", ready: "Order ready", daily: "Day-end summary", cash: "Cash difference", test: "Test" };
const STATUS: Record<string, string> = { queued: "Waiting to send", sent: "Sent", failed: "Failed" };

/** SMS: e-bill to customers, "order ready", the owner's day-end summary. */
export default function Sms() {
  const [s, setS] = useState<Settings | null>(null);
  const [key, setKey] = useState("");
  const [daily, setDaily] = useState("");
  const [testTo, setTestTo] = useState("");
  const [log, setLog] = useState<Msg[]>([]);
  const [saved, setSaved] = useState(false);
  const { busy, error, run } = useAction();

  const load = async () => {
    const x = await api<Settings>("/tenant/sms");
    setS(x); setDaily(x.daily_to.join(", "));
    setLog(await api<Msg[]>("/sms/messages"));
  };
  useEffect(() => { load(); }, []);
  useEffect(() => { const t = setInterval(() => api<Msg[]>("/sms/messages").then(setLog).catch(() => {}), 5000); return () => clearInterval(t); }, []);
  if (!s) return <p className="muted">Loading…</p>;
  const set = (patch: Partial<Settings>) => { setS({ ...s, ...patch }); setSaved(false); };

  const save = () => run(async () => {
    const out = await api<Settings>("/tenant/sms", { method: "PUT", body: {
      enabled: s.enabled, provider: s.provider, msg91_auth_key: key || null, sender_id: s.sender_id || null,
      template_ids: s.template_ids, send_bill: s.send_bill, send_ready: s.send_ready, send_daily: s.send_daily, send_cash: s.send_cash,
      send_alerts: s.send_alerts, alert_discount_bp: s.alert_discount_bp,
      daily_to: daily.split(/[,\s]+/).filter(Boolean) } });
    setS(out); setKey(""); setSaved(true);
  });

  const rows: [keyof Settings & ("send_bill" | "send_ready" | "send_daily" | "send_cash" | "send_alerts"), string, string][] = [
    ["send_bill", "bill", "Sent right after billing when the customer gave their number. The link opens the bill on their phone."],
    ["send_ready", "ready", "Takeaway and delivery orders, when the kitchen marks the order ready."],
    ["send_daily", "daily", "Each outlet's sales for the day, sent after the day closes at 4 am."],
    ["send_cash", "cash", "To the owner and the outlet's managers (their mobile from Staff) when a cashier's count doesn't match."],
    ["send_alerts", "alert", "At once, to the day-end numbers below (or the owner): a cancelled bill, a big discount, a complimentary bill, a corrected bill, the KOT printer not printing, the box having been off. They also show on Home."],
  ];

  return (
    <div className="stack">
      <h1>SMS</h1>
      <Tabs id="sms">
      <Tab label="Messages"><Card title="Messages">
        <Toggle checked={s.enabled} onChange={(v) => set({ enabled: v })} label="Send SMS" />
        <p className="muted small">Each message below shows the exact text to register for DLT; the ##…## parts are filled in for each customer.</p>
        <div className="sms-kinds">
          {rows.map(([flag, kind, help]) => (
            <div key={kind} className={`sms-kind ${s[flag] ? "" : "off"}`}>
              <Toggle checked={s[flag]} onChange={(v) => set({ [flag]: v } as Partial<Settings>)} label={s.templates[kind].label} />
              <p className="muted small">{help}</p>
              <p className="sms-text" title="Register this exact text; the ##…## parts are filled in for each message">{s.templates[kind].text}</p>
              {s.provider === "msg91" && (
                <label className="field"><span className="field-label">MSG91 template id</span>
                  <Input value={s.template_ids[kind] || ""} onChange={(e) => set({ template_ids: { ...s.template_ids, [kind]: e.target.value } })} /></label>
              )}
            </div>
          ))}
        </div>
        <label className="field" style={{ maxWidth: "22rem" }}><span className="field-label">Alert me for discounts of</span>
          <Select value={String(s.alert_discount_bp ?? 2000)} onChange={(e) => set({ alert_discount_bp: Number(e.target.value) })}>
            {[1000, 1500, 2000, 2500, 3000, 5000].map((bp) => <option key={bp} value={bp}>{bp / 100}% or more</option>)}
          </Select></label>
        <label className="field"><span className="field-label">Day-end summary and alerts go to</span>
          <Input placeholder="Mobile numbers, comma separated (up to 5)" value={daily} onChange={(e) => { setDaily(e.target.value); setSaved(false); }} /></label>
      </Card></Tab>
      <Tab label="Sending"><Card title="Sending">
        <label className="field"><span className="field-label">How to send</span>
          <Select value={s.provider} onChange={(e) => set({ provider: e.target.value as Settings["provider"] })}>
            <option value="log">Testing: write messages to the log only (nothing is sent)</option>
            <option value="msg91">MSG91 (your own account and sender ID)</option>
          </Select>
        </label>
        {s.provider === "msg91" && (
          <>
            <div className="grid2">
              <label className="field"><span className="field-label">MSG91 auth key</span>
                <Input type="password" placeholder={s.has_auth_key ? "Saved. Type a new one to change it" : "From MSG91 > API"} value={key} onChange={(e) => setKey(e.target.value)} /></label>
              <label className="field"><span className="field-label">Sender ID (6 letters)</span>
                <Input maxLength={6} placeholder="e.g. SHAKEW" value={s.sender_id || ""} onChange={(e) => set({ sender_id: e.target.value.toUpperCase().replace(/[^A-Z]/g, "") })} /></label>
            </div>
            <div className="banner small">
              Indian SMS rules (TRAI DLT): register your business, sender ID and each message text above on a DLT portal
              (Jio, Airtel, Vodafone Idea or BSNL), and get your website address whitelisted for the e-bill link. Then add the
              same texts as templates in MSG91 and paste each template id here. MSG91's support helps with DLT. The cost per SMS
              is charged by MSG91 to your account.
            </div>
          </>
        )}
        <div className="row">
          <Button disabled={busy} onClick={save}>Save</Button>
          {saved && <span className="ok">Saved</span>}
        </div>
        <ErrorText error={error} />
      </Card></Tab>
      <Tab label="Send a test"><Card title="Send a test">
        <div className="row">
          <Input inputMode="tel" placeholder="Your mobile" value={testTo} onChange={(e) => setTestTo(e.target.value)} />
          <Button disabled={busy || !testTo} onClick={() => run(async () => { await api("/tenant/sms/test", { body: { phone: testTo } }); setLog(await api<Msg[]>("/sms/messages")); })}>Send test SMS</Button>
        </div>
      </Card></Tab>
      <Tab label="Today's messages"><Card title="Today's messages">
        <table className="table">
          <thead><tr><th>Time</th><th>Message</th><th>To</th><th>Text</th><th>Status</th></tr></thead>
          <tbody>
            {log.map((m) => (
              <tr key={m.id}>
                <td>{new Date(m.created_at).toLocaleTimeString("en-IN", { timeStyle: "short" })}</td>
                <td>{KIND[m.kind] || m.kind}</td>
                <td>{m.to_phone}</td>
                <td className="sms-body">{m.body}</td>
                <td><span className={`sms-st st-${m.status}`}>{STATUS[m.status]}</span>
                  {m.last_error && <div className="small muted">{m.last_error}{m.status === "queued" ? ", will retry" : ""}</div>}</td>
              </tr>
            ))}
            {log.length === 0 && <tr><td colSpan={5} className="muted">No messages today.</td></tr>}
          </tbody>
        </table>
      </Card></Tab>
      </Tabs>
    </div>
  );
}
