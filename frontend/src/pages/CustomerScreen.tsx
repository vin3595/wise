import { useEffect, useRef, useState } from "react";
import { cleanMobile, useScreenSide, type AdItem } from "../counterScreen";
import { formatPaise } from "../money";
import { QrImage } from "../qr";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "clear", "0", "back"];
const ROWS = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"];
const ASK_TIMEOUT_MS = 60000;

/** Plays the outlet's ads one after another: pictures for their seconds, videos to the end. */
export function AdPlayer({ ads, fill }: { ads: AdItem[]; fill?: boolean }) {
  const [i, setI] = useState(0);
  const n = ads.length;
  const cur = n ? ads[i % n] : null;
  const next = () => setI((x) => (x + 1) % Math.max(1, n));
  useEffect(() => { if (i >= n) setI(0); }, [n, i]);
  useEffect(() => {
    if (!cur || cur.kind !== "image") return;
    const nx = ads[(i + 1) % n];
    if (nx && nx.kind === "image") { const im = new Image(); im.src = nx.url; }   // ready before its turn
    if (n < 2) return;
    const t = setTimeout(next, Math.max(3, cur.seconds) * 1000);
    return () => clearTimeout(t);
  }, [cur?.id, i]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!cur) return null;
  return (
    <div className={`ad ${fill ? "ad-fill" : ""}`} aria-hidden>
      {cur.kind === "image"
        ? <img key={`${cur.id}-${i}`} src={cur.url} alt="" onError={() => n > 1 && next()} />
        : <video key={`${cur.id}-${i}`} src={cur.url} autoPlay muted playsInline loop={n === 1} onEnded={next} onError={() => n > 1 && setTimeout(next, 1000)} />}
    </div>
  );
}

/** The second screen on a dual-screen POS, facing the customer. Touch only: the customer types
 *  their own mobile number (and name if they like); it goes straight into the cashier's cart. */
export default function CustomerScreen() {
  const { s, send } = useScreenSide();
  const [step, setStep] = useState<"phone" | "name">("phone");
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [bad, setBad] = useState(false);
  // Started by the counter's start-up file (?kiosk=1): Chrome is already full screen, nothing to tap.
  const [full, setFull] = useState(() => new URLSearchParams(location.search).has("kiosk"));
  const lastTouch = useRef(Date.now());
  const asking = !!s?.ask && !s.qr && !s.thanks;

  // Nobody can work this screen except to answer when the cashier asks: no menus, no zoom, no scrolling.
  useEffect(() => {
    const stop = (e: Event) => e.preventDefault();
    document.addEventListener("contextmenu", stop);
    document.addEventListener("selectstart", stop);
    document.addEventListener("gesturestart", stop);
    const wheel = (e: WheelEvent) => { if (e.ctrlKey) e.preventDefault(); };
    document.addEventListener("wheel", wheel, { passive: false });
    const onFs = () => setFull(!!document.fullscreenElement || new URLSearchParams(location.search).has("kiosk"));
    document.addEventListener("fullscreenchange", onFs);
    return () => { document.removeEventListener("contextmenu", stop); document.removeEventListener("selectstart", stop);
      document.removeEventListener("gesturestart", stop); document.removeEventListener("wheel", wheel); document.removeEventListener("fullscreenchange", onFs); };
  }, []);
  // A customer who walks away: give the counter back after a minute.
  useEffect(() => {
    if (!asking) return;
    lastTouch.current = Date.now();
    const t = setInterval(() => { if (Date.now() - lastTouch.current > ASK_TIMEOUT_MS) send({ t: "timeout" }); }, 2000);
    return () => clearInterval(t);
  }, [asking, send]);

  // A fresh ask (new customer) starts from the number again.
  useEffect(() => {
    if (s?.ask) { setStep("phone"); setPhone(s.phone || ""); setName(s.name || ""); setBad(false); }
  }, [s?.ask]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (s) { document.documentElement.style.setProperty("--brand", s.brand.primary_color); document.title = `${s.brand.app_name} · Welcome`; }
  }, [s?.brand.primary_color, s?.brand.app_name]); // eslint-disable-line react-hooks/exhaustive-deps

  // Full screen is turned on once by staff when setting up (the browser only allows it from a tap on this screen).
  const goFull = () => {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.().then(() => setFull(true)).catch(() => undefined);
  };
  const touched = () => { lastTouch.current = Date.now(); };
  const tapDigit = (k: string) => {
    setBad(false);
    if (k === "clear") setPhone("");
    else if (k === "back") setPhone((p) => p.slice(0, -1));
    else setPhone((p) => (p.length < 10 ? p + k : p));
  };
  const nextFromPhone = () => {
    if (!cleanMobile(phone)) { setBad(true); return; }
    setStep("name");
  };
  const done = (withName: boolean) => {
    const m = cleanMobile(phone);
    if (!m) { setStep("phone"); setBad(true); return; }
    send({ t: "customer", phone: m, name: withName ? name.trim().replace(/\s+/g, " ") : "" });
  };

  if (!s) {
    return <div className="cs cs-wait" onClick={goFull}><p>Waiting for the billing counter…</p><p className="muted small">Open Orders on this computer. This window shows on the customer's screen.</p></div>;
  }
  const idle = !s.lines.length && !s.qr && !s.thanks && !s.ask;
  if (idle) {
    return (
      <div className="cs cs-locked cs-idle-full" onClick={full ? undefined : goFull}>
        {s.ads.length ? <AdPlayer ads={s.ads} fill /> : (
          <div className="cs-welcome">
            {s.brand.logo_url ? <img src={s.brand.logo_url} alt="" /> : <span className="cs-mark big">{s.brand.app_name[0]}</span>}
            <strong>{s.brand.app_name}</strong><span>Welcome! Please order at the counter.</span>
          </div>
        )}
        {!full && <p className="cs-hint">Staff: tap once for full screen</p>}
      </div>
    );
  }
  const shown = s.phone && !s.ask ? `${s.phone.slice(0, 2)}XXXXXX${s.phone.slice(-2)}` : null;
  return (
    <div className={`cs ${asking ? "" : "cs-locked"}`} onClick={full ? touched : goFull} onTouchStart={touched}>
      <header className="cs-head">
        {s.brand.logo_url ? <img src={s.brand.logo_url} alt="" /> : <span className="cs-mark">{s.brand.app_name[0]}</span>}
        <div><strong>{s.brand.app_name}</strong><span>{s.brand.outlet}</span></div>
      </header>

      <main className="cs-body">
        <section className="cs-cart" aria-label="Your order">
          {s.lines.length === 0 ? <p className="cs-hello">Welcome!</p> : <>
            <ul>
              {s.lines.map((l, i) => <li key={i}><span className="q">{l.qty} ×</span><span className="n">{l.name}</span><span className="a">{formatPaise(l.amount_paise)}</span></li>)}
            </ul>
            {s.discount_paise > 0 && <div className="cs-row"><span>Discount</span><span>−{formatPaise(s.discount_paise)}</span></div>}
            <div className="cs-total"><span>Total</span><strong>{formatPaise(s.total_paise)}</strong></div>
            {shown && <p className="cs-who">Bill to {s.name ? `${s.name}, ` : ""}{shown}</p>}
          </>}
        </section>

        <section className="cs-side">
          {s.thanks ? (
            <div className="cs-thanks" role="status">
              <strong>Thank you!</strong>
              <span>{formatPaise(s.thanks.amount_paise)} paid{s.thanks.token_no ? `. Your token is ${s.thanks.token_no}` : ""}.</span>
              {s.thanks.sms && <span className="muted">Your bill is on its way to your phone.</span>}
            </div>
          ) : s.qr ? (
            <div className="cs-qr">
              <h2>Scan to pay {formatPaise(s.qr.amount_paise)}</h2>
              <QrImage text={s.qr.upi} size={300} label={`UPI QR for ${formatPaise(s.qr.amount_paise)}`} />
              <p>Any UPI app. The amount is filled in.</p>
            </div>
          ) : asking ? (
            step === "phone" ? (
              <div className="cs-pad">
                <h2>Your mobile number</h2>
                <p className="muted">For your e-bill and rewards</p>
                <output className={`cs-phone ${bad ? "bad" : ""}`} aria-live="polite">{phone.padEnd(10, "·").replace(/^(.{5})/, "$1 ")}</output>
                {bad && <p className="cs-err">Please check the number: 10 digits, starting with 6, 7, 8 or 9.</p>}
                <div className="cs-keys">
                  {KEYS.map((k) => <button key={k} className={k.length > 1 ? "fn" : ""} onClick={() => tapDigit(k)}
                    aria-label={k === "back" ? "Delete last digit" : k === "clear" ? "Clear" : k}>{k === "back" ? "⌫" : k === "clear" ? "Clear" : k}</button>)}
                </div>
                <div className="cs-actions">
                  <button className="cs-skip" onClick={() => send({ t: "skip" })}>Skip</button>
                  <button className="cs-go" disabled={phone.length < 10} onClick={nextFromPhone}>Next</button>
                </div>
              </div>
            ) : (
              <div className="cs-pad">
                <h2>Your name <span className="muted">(optional)</span></h2>
                <output className="cs-name">{name || " "}</output>
                <div className="cs-kbd">
                  {ROWS.map((r) => <div key={r}>{[...r].map((c) => <button key={c} onClick={() => setName((n) => (n.length < 40 ? n + (n === "" || n.endsWith(" ") ? c : c.toLowerCase()) : n))}>{c}</button>)}</div>)}
                  <div>
                    <button className="wide" onClick={() => setName((n) => (n && !n.endsWith(" ") && n.length < 40 ? n + " " : n))}>Space</button>
                    <button className="fn" onClick={() => setName((n) => n.slice(0, -1))} aria-label="Delete last letter">⌫</button>
                  </div>
                </div>
                <div className="cs-actions">
                  <button className="cs-skip" onClick={() => setStep("phone")}>Back</button>
                  <button className="cs-skip" onClick={() => done(false)}>Skip name</button>
                  <button className="cs-go" onClick={() => done(true)}>Done</button>
                </div>
              </div>
            )
          ) : s.ads.length ? (
            <div className="cs-adside"><AdPlayer ads={s.ads} /></div>
          ) : (
            <div className="cs-idle"><p>Your order is shown here as it's made.</p></div>
          )}
        </section>
      </main>
      {!full && <p className="cs-hint">Staff: tap once for full screen</p>}
    </div>
  );
}
