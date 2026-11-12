import { useEffect, useState, type ChangeEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useSession } from "../session";
import type { TokenOut } from "../types";
import { Button, ErrorText, Field, Input, useAction } from "../ui";

export default function Signup() {
  const { signedIn } = useSession();
  const [form, setForm] = useState({ restaurant_name: "", login_id: "", owner_name: "", email: "", phone: "" });
  const [idStatus, setIdStatus] = useState<{ available: boolean; reason: string | null } | null>(null);
  const [sent, setSent] = useState<{ sent_to: string; dev_otp: string | null } | null>(null);
  const [otp, setOtp] = useState("");
  const { busy, error, run } = useAction();
  const set = (k: keyof typeof form) => (e: ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  useEffect(() => {
    const id = form.login_id.trim().toLowerCase();
    if (id.length < 3) { setIdStatus(null); return; }
    const t = setTimeout(() => {
      api<{ available: boolean; reason: string | null }>(`/auth/login-id-available?login_id=${encodeURIComponent(id)}`)
        .then(setIdStatus).catch(() => setIdStatus(null));
    }, 350);
    return () => clearTimeout(t);
  }, [form.login_id]);

  if (sent) {
    return (
      <div className="auth-page"><div className="auth-card">
        <h1>Verify your mobile</h1>
        <p className="muted">We sent a 6-digit code to {sent.sent_to}.</p>
        {sent.dev_otp && <p className="dev-note">Development mode code: <strong>{sent.dev_otp}</strong></p>}
        <form onSubmit={(e) => {
          e.preventDefault();
          run(async () => signedIn(await api<TokenOut>("/auth/signup/verify", { body: { ...form, otp } })));
        }}>
          <Field label="Code">
            <Input value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
              inputMode="numeric" autoComplete="one-time-code" autoFocus />
          </Field>
          <ErrorText error={error} />
          <Button disabled={busy || otp.length !== 6}>Create restaurant</Button>
          <button type="button" className="link" onClick={() => setSent(null)}>Edit details</button>
        </form>
      </div></div>
    );
  }

  return (
    <div className="auth-page"><div className="auth-card">
      <h1>Create your restaurant</h1>
      <p className="muted">14-day free trial. Your staff and customers will see your name and logo, not ours.</p>
      <form onSubmit={(e) => {
        e.preventDefault();
        run(async () => setSent(await api("/auth/signup/start", { body: form })));
      }}>
        <Field label="Restaurant name"><Input value={form.restaurant_name} onChange={(e) => {
          // Keep suggesting a login ID from the name until the owner edits it themselves.
          const name = e.target.value;
          setForm((f) => ({ ...f, restaurant_name: name,
            login_id: f.login_id && f.login_id !== slug(f.restaurant_name) ? f.login_id : slug(name) }));
        }} required /></Field>
        <Field label="Login ID" hint={idStatus ? (idStatus.available ? "Available" : idStatus.reason || "Not available") : "Your staff type this once on each device. Also your web address."}>
          <Input value={form.login_id} onChange={set("login_id")} autoCapitalize="none" required />
        </Field>
        <Field label="Your name"><Input value={form.owner_name} onChange={set("owner_name")} required /></Field>
        <Field label="Email"><Input type="email" value={form.email} onChange={set("email")} required /></Field>
        <Field label="Mobile number"><Input inputMode="tel" value={form.phone} onChange={set("phone")} required /></Field>
        <ErrorText error={error} />
        <Button disabled={busy || idStatus?.available === false}>Send verification code</Button>
      </form>
      <p className="muted center">Already set up? <Link to="/setup">Sign in</Link></p>
    </div></div>
  );
}

function slug(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 30);
}
