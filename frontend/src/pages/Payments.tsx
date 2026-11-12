import { useEffect, useState } from "react";
import { api } from "../api";
import { Button, Card, ErrorText, Field, Input, Select, Toggle, useAction, Tab, Tabs } from "../ui";
import type { Outlet } from "../types";
import { QrImage } from "../qr";
import { formatPaise } from "../money";

interface PaySettings { enabled: boolean; provider: "razorpay" | "paytm" | "test"; paytm_mid: string | null; has_paytm_key: boolean; paytm_client_id: string | null;
  paytm_live: boolean; paytm_callback_url: string; qr_minutes: number; print_qr: boolean; key_id: string | null; has_key_secret: boolean; has_webhook_secret: boolean; webhook_url: string; webhook_event: string;
  pos_enabled: boolean; pos_provider: "razorpay_pos" | "test"; pos_live: boolean; pos_username: string | null; has_pos_app_key: boolean }
interface Terminal { id: string; name: string; device_id: string; is_active: boolean; outlet_id: string }

/** UPI that confirms itself, through the restaurant's own Razorpay account. */
export default function Payments() {
  const [s, setS] = useState<PaySettings | null>(null);
  const [secret, setSecret] = useState("");
  const [hook, setHook] = useState("");
  const [saved, setSaved] = useState(false);
  const [posKey, setPosKey] = useState("");
  const [ptKey, setPtKey] = useState("");
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [outletId, setOutletId] = useState("");
  const [terms, setTerms] = useState<Terminal[]>([]);
  const [tName, setTName] = useState(""), [tDev, setTDev] = useState("");
  const { busy, error, run } = useAction();
  useEffect(() => { api<PaySettings>("/tenant/payments").then(setS); api<Outlet[]>("/tenant/outlets").then((o) => { setOutlets(o); setOutletId(o[0]?.id || ""); }); }, []);
  useEffect(() => { if (outletId) api<Terminal[]>(`/outlets/${outletId}/card-terminals`).then(setTerms); }, [outletId]);
  if (!s) return <p className="muted">Loading…</p>;
  const set = (p: Partial<PaySettings>) => { setS({ ...s, ...p }); setSaved(false); };
  const save = () => run(async () => {
    setS(await api<PaySettings>("/tenant/payments", { method: "PUT", body: { enabled: s.enabled, provider: s.provider, key_id: s.key_id || "", key_secret: secret || null, webhook_secret: hook || null,
      pos_enabled: s.pos_enabled, pos_provider: s.pos_provider, pos_live: s.pos_live, pos_username: s.pos_username || "", pos_app_key: posKey || null,
      paytm_mid: s.paytm_mid || "", paytm_key: ptKey || null, paytm_client_id: s.paytm_client_id || "", paytm_live: s.paytm_live, qr_minutes: s.qr_minutes, print_qr: s.print_qr } }));
    setSecret(""); setHook(""); setPosKey(""); setPtKey(""); setSaved(true);
  });
  return (
    <div className="stack">
      <h1>Payments</h1>
      <Tabs id="payments">
      <Tab label="UPI"><Card title="UPI that confirms itself">
        <p className="muted">Each bill gets its own UPI QR for the exact amount. When the customer pays, the bill prints by itself with the UPI reference: nobody checks a phone, and a fake "payment done" screen can't fool the counter. Money goes straight to your own Paytm or Razorpay account.</p>
        <Toggle checked={s.enabled} onChange={(v) => set({ enabled: v })} label="Use it at the billing counters" />
        <div className="seg three">
          <button className={s.provider === "test" ? "active" : ""} onClick={() => set({ provider: "test" })}>Test (no real money)</button>
          <button className={s.provider === "paytm" ? "active" : ""} onClick={() => set({ provider: "paytm" })}>Paytm Dynamic QR</button>
          <button className={s.provider === "razorpay" ? "active" : ""} onClick={() => set({ provider: "razorpay" })}>Razorpay</button>
        </div>
        {s.provider === "paytm" && <>
          <ol className="small steps">
            <li>You need Paytm for Business with <strong>Dynamic QR for billing software</strong> switched on for your MID. If Petpooja already prints Paytm QRs on your bills, it is on: ask Paytm to also give it to Wise.</li>
            <li>Paytm dashboard &gt; Developer settings &gt; API keys: copy the <strong>MID</strong> and <strong>Merchant key</strong>. Start with the test (staging) keys and keep "Paytm's test server" on.</li>
            <li>Ask Paytm to set the payment notification (server-to-server) URL to the one below. The box needs its public address (<code>sudo wise tunnel</code>). Without it, it still works: the box asks Paytm every few seconds while a QR is out.</li>
          </ol>
          <div className="grid2">
            <Field label="MID (Merchant ID)"><Input value={s.paytm_mid || ""} onChange={(e) => set({ paytm_mid: e.target.value })} /></Field>
            <Field label="Merchant key" hint={s.has_paytm_key ? "Saved. Type a new one only to change it." : "16 characters, from the Paytm dashboard"}><Input type="password" value={ptKey} onChange={(e) => setPtKey(e.target.value)} /></Field>
            <Field label="Client ID" hint="Only if Paytm gave you one (e.g. C11)"><Input value={s.paytm_client_id || ""} onChange={(e) => set({ paytm_client_id: e.target.value })} /></Field>
            <Field label="Notification URL (give this to Paytm)"><Input readOnly value={s.paytm_callback_url} onFocus={(e) => e.target.select()} /></Field>
          </div>
          <label className="toggle"><input type="checkbox" checked={!s.paytm_live} onChange={(e) => set({ paytm_live: !e.target.checked })} /><span>Use Paytm's test server (no real money)</span></label>
          <PaytmCheck saved={!!s.paytm_mid && s.has_paytm_key} live={s.paytm_live} />
          <p className="muted small">Paytm's own pricing applies to each payment; ask them to confirm the rate for Dynamic QR on your MID in writing. Refunds are made in the Paytm dashboard.</p>
        </>}
        <div className="grid2" style={{ maxWidth: "36rem" }}>
          <Field label="QR stays payable for (minutes)" hint={s.provider === "paytm" ? "Paytm allows 10 unless they've raised it for you" : undefined}>
            <Input type="number" min={2} max={1440} value={String(s.qr_minutes)} onChange={(e) => set({ qr_minutes: Math.max(2, Math.min(1440, Number(e.target.value) || 10)) })} /></Field>
        </div>
        <Toggle checked={s.print_qr} onChange={(v) => set({ print_qr: v })} label="Print the QR on the bill as soon as UPI is picked (for tables, or counters without a screen the customer can see)" />
        {s.provider === "test" && <p className="muted small">The counter shows a pretend QR with a "Customer paid (test)" button. Use it to train staff.</p>}
        {s.provider === "razorpay" && <>
          <ol className="small steps">
            <li>Open a Razorpay account (business KYC) and ask Razorpay to activate <strong>UPI QR Codes</strong>.</li>
            <li>Razorpay Dashboard &gt; Account &amp; Settings &gt; API Keys: generate keys, paste them below.</li>
            <li>Dashboard &gt; Webhooks &gt; Add: URL below, event <code>{s.webhook_event}</code>, set a secret and paste it here. The box needs its public address (<code>sudo wise tunnel</code>). Without the webhook it still works: the box asks Razorpay every few seconds while the QR is on screen.</li>
          </ol>
          <div className="grid2">
            <Field label="Key ID"><Input value={s.key_id || ""} placeholder="rzp_live_…" onChange={(e) => set({ key_id: e.target.value })} /></Field>
            <Field label="Key Secret" hint={s.has_key_secret ? "Saved. Type a new one only to change it." : undefined}><Input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} /></Field>
            <Field label="Webhook URL"><Input readOnly value={s.webhook_url} onFocus={(e) => e.target.select()} /></Field>
            <Field label="Webhook secret" hint={s.has_webhook_secret ? "Saved." : "The secret you typed in Razorpay's webhook form"}><Input type="password" value={hook} onChange={(e) => setHook(e.target.value)} /></Field>
          </div>
          <p className="muted small">Razorpay charges its fee on each payment (UPI is usually free or very low for small amounts; check your plan). Refunds are made in the Razorpay dashboard.</p>
        </>}
        <ErrorText error={error} />
        {saved && <p className="ok">Saved.</p>}
        <Button disabled={busy} onClick={save}>Save</Button>
      </Card></Tab>
      <Tab label="Card machines"><Card title="Card machines (Razorpay POS)">
        <p className="muted">Tap Card on the counter and the bill amount appears on that counter's card machine by itself. When the card is approved, the bill prints with the card's last 4 digits and approval code. No typing the amount, no wrong amounts.</p>
        <Toggle checked={s.pos_enabled} onChange={(v) => set({ pos_enabled: v })} label="Link the card machines" />
        <div className="seg two">
          <button className={s.pos_provider === "test" ? "active" : ""} onClick={() => set({ pos_provider: "test" })}>Test (no machine)</button>
          <button className={s.pos_provider === "razorpay_pos" ? "active" : ""} onClick={() => set({ pos_provider: "razorpay_pos" })}>Razorpay POS</button>
        </div>
        {s.pos_provider === "test" && <p className="muted small">The counter shows "Card approved (test)" and "Declined (test)" buttons instead of talking to a machine. Use it to train staff.</p>}
        {s.pos_provider === "razorpay_pos" && <>
          <ol className="small steps">
            <li>Ask Razorpay POS for an Android Smart POS for each billing counter, and for <strong>"P2P / push to POS" API access for billing software</strong>: they give a username, an app key and each machine's device id.</li>
            <li>Ask for their demo (test) credentials too, and keep "Razorpay's test server" on until a test card payment works.</li>
          </ol>
          <div className="grid2">
            <Field label="Username"><Input value={s.pos_username || ""} onChange={(e) => set({ pos_username: e.target.value })} /></Field>
            <Field label="App key" hint={s.has_pos_app_key ? "Saved. Type a new one only to change it." : undefined}><Input type="password" value={posKey} onChange={(e) => setPosKey(e.target.value)} /></Field>
          </div>
          <label className="toggle"><input type="checkbox" checked={!s.pos_live} onChange={(e) => set({ pos_live: !e.target.checked })} /><span>Use Razorpay's test server (no real money)</span></label>
        </>}
        <Button disabled={busy} onClick={save}>Save</Button>
        <h3>Machines at each outlet</h3>
        {outlets.length > 1 && <Select value={outletId} onChange={(e) => setOutletId(e.target.value)} aria-label="Outlet">{outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</Select>}
        <table className="table"><tbody>
          {terms.map((t) => <tr key={t.id} className={t.is_active ? "" : "muted"}><td>{t.name}</td><td className="small">{t.device_id}</td>
            <td><button className="link" onClick={() => run(async () => { await api(`/card-terminals/${t.id}`, { method: "PATCH", body: { name: t.name, device_id: t.device_id, is_active: !t.is_active } }); setTerms(await api<Terminal[]>(`/outlets/${outletId}/card-terminals`)); })}>{t.is_active ? "Stop using" : "Use again"}</button></td></tr>)}
          {terms.length === 0 && <tr><td className="muted">No card machines yet.</td></tr>}
        </tbody></table>
        <div className="row">
          <Input placeholder="Name, e.g. Counter 1" value={tName} onChange={(e) => setTName(e.target.value)} />
          <Input placeholder="Device id, e.g. 1490012345|ezetap_android" value={tDev} onChange={(e) => setTDev(e.target.value)} />
          <Button variant="ghost" disabled={busy || !tName.trim() || tDev.trim().length < 3} onClick={() => run(async () => { await api(`/outlets/${outletId}/card-terminals`, { body: { name: tName, device_id: tDev } }); setTName(""); setTDev(""); setTerms(await api<Terminal[]>(`/outlets/${outletId}/card-terminals`)); })}>Add machine</Button>
        </div>
        <p className="muted small">Each billing counter is asked once which machine sits next to it. If a machine can't be reached, the counter falls back to typing the amount on the machine and tapping Card received.</p>
      </Card></Tab>
      </Tabs>
    </div>
  );
}

interface TestQr { order_id: string; upi_string: string; amount_paise: number; expires_at: string; live: boolean }
interface TestState { state: "paid" | "pending" | "failed" | "unknown"; message: string | null; reference: string | null; amount_paise: number | null }

/** A real ₹1 Paytm QR, not linked to any bill: proves the MID, key, server and status check work. */
function PaytmCheck({ saved, live }: { saved: boolean; live: boolean }) {
  const [qr, setQr] = useState<TestQr | null>(null);
  const [st, setSt] = useState<TestState | null>(null);
  const { busy, error, run } = useAction();
  useEffect(() => {
    if (!qr || st?.state === "paid" || st?.state === "failed") return;
    const t = setInterval(async () => { try { setSt(await api<TestState>(`/tenant/payments/test-qr/${qr.order_id}`)); } catch { /* keep asking */ } }, 3000);
    return () => clearInterval(t);
  }, [qr, st?.state]);
  return (
    <div className="paytm-check">
      <Button variant="ghost" disabled={busy || !saved} onClick={() => run(async () => { setSt(null); setQr(await api<TestQr>("/tenant/payments/test-qr", { method: "POST" })); })}>
        {qr ? "Make a new ₹1 QR" : "Pay ₹1 to check"}
      </Button>
      {!saved && <p className="muted small">Save the MID and merchant key first.</p>}
      <ErrorText error={error} />
      {qr && <div className="upi-box">
        <QrImage text={qr.upi_string} size={180} label="₹1 test QR" />
        <div>
          <p><strong>Scan and pay {formatPaise(qr.amount_paise)}</strong> {qr.live ? "(real money, goes to your Paytm account)" : "(Paytm test server: use Paytm's test app or credentials)"}</p>
          {(!st || st.state === "pending" || st.state === "unknown") && <p className="upi-wait"><span className="dot-pulse" aria-hidden /> Waiting for Paytm{st?.message ? `: ${st.message}` : "…"}</p>}
          {st?.state === "paid" && <p className="ok">Works ✓ Paytm confirmed {formatPaise(st.amount_paise || 0)}, reference {st.reference}. No bill was made.</p>}
          {st?.state === "failed" && <p className="error">Paytm says the payment failed: {st.message}</p>}
        </div>
      </div>}
    </div>
  );
}
