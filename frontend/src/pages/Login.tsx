import { useEffect, useState } from "react";
import { api } from "../api";
import { useSession } from "../session";
import type { TokenOut } from "../types";
import { Button, ErrorText, Field, Input, Select, useAction } from "../ui";

type Mode = "staff" | "owner";

export default function Login() {
  const { branding, deviceOutletId } = useSession();
  const [mode, setMode] = useState<Mode>(deviceOutletId ? "staff" : "owner");
  if (!branding) return null;
  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-logo">
          {branding.logo_url ? <img src={branding.logo_url} alt={branding.name} /> : <span className="logo-fallback big">{branding.app_name[0]}</span>}
          <h1>{branding.app_name}</h1>
        </div>
        <div className="tabs">
          <button className={mode === "staff" ? "active" : ""} onClick={() => setMode("staff")}>Staff PIN</button>
          <button className={mode === "owner" ? "active" : ""} onClick={() => setMode("owner")}>Owner / manager</button>
        </div>
        {mode === "staff" ? <PinLogin /> : <OtpLogin />}
        <DeviceFooter />
      </div>
      {branding.show_powered_by && <footer className="powered">Powered by Wise</footer>}
    </div>
  );
}

function DeviceFooter() {
  const { forgetRestaurant, loginId } = useSession();
  return (
    <p className="muted center small">
      Restaurant ID: <strong>{loginId}</strong> ·{" "}
      <button className="link" onClick={forgetRestaurant}>Use a different restaurant</button>
    </p>
  );
}

function OtpLogin() {
  const { loginId, signedIn } = useSession();
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [sent, setSent] = useState<{ sent_to: string; dev_otp: string | null } | null>(null);
  const { busy, error, run } = useAction();

  if (!sent) {
    return (
      <form onSubmit={(e) => {
        e.preventDefault();
        run(async () => setSent(await api("/auth/otp/request", { body: { login_id: loginId, identifier } })));
      }}>
        <Field label="Email or mobile number">
          <Input value={identifier} onChange={(e) => setIdentifier(e.target.value)} inputMode="email" autoFocus required />
        </Field>
        <ErrorText error={error} />
        <Button disabled={busy}>Send code</Button>
      </form>
    );
  }
  return (
    <form onSubmit={(e) => {
      e.preventDefault();
      run(async () => signedIn(await api<TokenOut>("/auth/otp/verify", { body: { login_id: loginId, identifier, otp } })));
    }}>
      <p className="muted">If that account exists, we sent a 6-digit code to {sent.sent_to}.</p>
      {sent.dev_otp && <p className="dev-note">Development mode code: <strong>{sent.dev_otp}</strong></p>}
      <Field label="Code">
        <Input value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
          inputMode="numeric" autoComplete="one-time-code" autoFocus required />
      </Field>
      <ErrorText error={error} />
      <Button disabled={busy || otp.length !== 6}>Sign in</Button>
      <button type="button" className="link" onClick={() => { setSent(null); setOtp(""); }}>Change number or email</button>
    </form>
  );
}

function PinLogin() {
  const { loginId, deviceOutletId, setDeviceOutlet, signedIn } = useSession();
  const [outlets, setOutlets] = useState<{ id: string; name: string }[]>([]);
  const [pin, setPin] = useState("");
  const { busy, error, run, setError } = useAction();

  useEffect(() => {
    api<{ id: string; name: string }[]>(`/auth/outlets?login_id=${encodeURIComponent(loginId || "")}`)
      .then(setOutlets)
      .catch((e) => setError(e.message));
  }, [loginId, setError]);

  const outletName = outlets.find((o) => o.id === deviceOutletId)?.name;

  function press(d: string) {
    if (busy) return;
    const next = (pin + d).slice(0, 4);
    setPin(next);
    if (next.length === 4) {
      run(async () => {
        try {
          await signedIn(await api<TokenOut>("/auth/pin", { body: { login_id: loginId, outlet_id: deviceOutletId, pin: next } }));
        } finally {
          setPin("");
        }
      });
    }
  }

  if (!deviceOutletId) {
    return (
      <div>
        <p className="muted">Which outlet is this device at? This is saved on the device.</p>
        <Field label="Outlet">
          <Select defaultValue="" onChange={(e) => e.target.value && setDeviceOutlet(e.target.value)}>
            <option value="" disabled>Choose outlet</option>
            {outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </Select>
        </Field>
        {outlets.length === 0 && <p className="muted small">No outlets yet. The owner can add one after signing in.</p>}
        <ErrorText error={error} />
      </div>
    );
  }

  return (
    <div className="pin-login">
      <p className="muted center">
        {outletName || "Outlet"} · <button className="link" onClick={() => setDeviceOutlet(null)}>change</button>
      </p>
      <div className="pin-dots" aria-label={`${pin.length} of 4 digits entered`}>
        {[0, 1, 2, 3].map((i) => <span key={i} className={i < pin.length ? "on" : ""} />)}
      </div>
      <ErrorText error={error} />
      <div className="pin-pad">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => <button key={d} onClick={() => press(d)}>{d}</button>)}
        <button onClick={() => setPin("")} aria-label="Clear">C</button>
        <button onClick={() => press("0")}>0</button>
        <button onClick={() => setPin(pin.slice(0, -1))} aria-label="Delete">⌫</button>
      </div>
    </div>
  );
}
