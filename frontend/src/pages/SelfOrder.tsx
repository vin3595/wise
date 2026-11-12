import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { usePolling } from "../hooks";
import { formatPaise } from "../money";
import { QrImage } from "../qr";
import type { OutletMenu, OutletMenuItem, OutletMenuVariant, SelfOrderStatus } from "../types";
import { Button, ErrorText, FoodDot, Input } from "../ui";
import { ItemArt } from "../art";

interface Info {
  outlet_name: string; app_name: string; logo_url: string | null; primary_color: string; table?: string | null;
  car_number_required: boolean; upi_available?: boolean; show_powered_by: boolean; menu: OutletMenu;
}
interface Line { key: string; item: OutletMenuItem; variant: OutletMenuVariant; addonIds: string[]; qty: number }
type Step = "menu" | "details" | "pay" | "status";

const IDLE_MS = 90_000;
const unit = (l: Line) => l.variant.price_paise + l.item.addon_groups.flatMap((g) => g.addons)
  .filter((a) => a.id && l.addonIds.includes(a.id)).reduce((s, a) => s + a.price_paise, 0);

/** Customer self-ordering. mode="qr": phone at the table (/q/<key>?t=T4). mode="kiosk": touch screen (/kiosk/<key>). */
export default function SelfOrder({ mode, outletKey }: { mode: "qr" | "kiosk"; outletKey: string }) {
  const kiosk = mode === "kiosk";
  const table = new URLSearchParams(location.search).get("t");
  const storeKey = `wise.self.${outletKey}`;
  const [info, setInfo] = useState<Info | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [cat, setCat] = useState<string | null>(null);
  const [cart, setCart] = useState<Line[]>([]);
  const [picking, setPicking] = useState<OutletMenuItem | null>(null);
  const [step, setStep] = useState<Step>("menu");
  const [orderType, setOrderType] = useState<"dine_in" | "takeaway">("dine_in");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [car, setCar] = useState("");
  const [quote, setQuote] = useState<{ total_paise: number; tax_paise: number } | null>(null);
  const [orderId, setOrderId] = useState<string | null>(() => (kiosk ? null : localStorage.getItem(storeKey)));
  const [status, setStatus] = useState<SelfOrderStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastTouch = useRef(Date.now());

  useEffect(() => {
    const path = kiosk ? `/public/kiosk/${outletKey}` : `/public/qr/${outletKey}${table ? `?t=${encodeURIComponent(table)}` : ""}`;
    api<Info>(path).then((i) => {
      setInfo(i);
      setCat(i.menu.categories[0]?.id ?? null);
      document.documentElement.style.setProperty("--brand", i.primary_color);
      document.title = i.app_name;
      if (!kiosk && !i.table) setOrderType("takeaway");
    }).catch((e) => setLoadError(e.message));
  }, [kiosk, outletKey, table]);

  useEffect(() => { if (orderId) setStep("status"); }, [orderId]);

  function reset() {
    setCart([]); setStep("menu"); setName(""); setPhone(""); setCar(""); setQuote(null);
    setOrderId(null); setStatus(null); setError(null); setOrderType("dine_in");
    if (!kiosk) localStorage.removeItem(storeKey);
  }

  // Kiosk: start over after 90 s untouched (or 20 s after showing the token).
  useEffect(() => {
    if (!kiosk) return;
    const touch = () => { lastTouch.current = Date.now(); };
    window.addEventListener("pointerdown", touch);
    const t = setInterval(() => {
      const idle = Date.now() - lastTouch.current;
      const done = status && status.status !== "pending";
      if ((cart.length || step !== "menu") && idle > (done ? 20_000 : step === "pay" ? IDLE_MS * 2 : IDLE_MS)) reset();
    }, 2000);
    return () => { window.removeEventListener("pointerdown", touch); clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kiosk, cart.length, step, status]);

  usePolling(async () => {
    if (!orderId) return;
    const s = await api<SelfOrderStatus>(`/public/qr/${outletKey}/orders/${orderId}${kiosk ? "?kiosk=true" : ""}`);
    setStatus(s);
  }, 3000, [orderId]);

  const items = useMemo(() => info?.menu.items.filter((i) => i.category_id === cat) ?? [], [info, cat]);
  const count = cart.reduce((s, l) => s + l.qty, 0);
  const subtotal = cart.reduce((s, l) => s + unit(l) * l.qty, 0);

  function add(item: OutletMenuItem, variant: OutletMenuVariant, addonIds: string[] = []) {
    const key = `${variant.variant_id}|${[...addonIds].sort().join(",")}`;
    setCart((c) => (c.some((l) => l.key === key) ? c.map((l) => (l.key === key ? { ...l, qty: l.qty + 1 } : l)) : [...c, { key, item, variant, addonIds, qty: 1 }]));
  }
  const setQty = (key: string, q: number) => setCart((c) => (q <= 0 ? c.filter((l) => l.key !== key) : c.map((l) => (l.key === key ? { ...l, qty: q } : l))));
  const lines = () => cart.map((l) => ({ variant_id: l.variant.variant_id, qty: l.qty, addon_ids: l.addonIds }));

  async function toDetails() {
    setError(null);
    if (kiosk) {
      try { setQuote(await api(`/public/kiosk/${outletKey}/quote`, { body: { items: lines() } })); } catch (e) { setError((e as Error).message); return; }
    }
    setStep("details");
  }

  async function place(pay?: "upi" | "counter") {
    setBusy(true);
    setError(null);
    try {
      const s = kiosk
        ? await api<SelfOrderStatus>(`/public/kiosk/${outletKey}/orders`, { body: { order_type: orderType, customer_name: name, customer_phone: phone, car_number: car || null, pay, items: lines() } })
        : await api<SelfOrderStatus>(`/public/qr/${outletKey}/orders`, { body: { table: info?.table || null, takeaway: orderType === "takeaway", customer_name: name, customer_phone: phone, car_number: car || null, items: lines() } });
      setStatus(s);
      setOrderId(s.order_id);
      if (!kiosk) localStorage.setItem(storeKey, s.order_id);
      setStep(kiosk && s.status === "pending" ? "pay" : "status");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <div className="splash">{loadError}</div>;
  if (!info) return <div className="splash">Loading menu…</div>;

  const header = (
    <header className="so-head">
      {info.logo_url ? <img src={info.logo_url} alt="" /> : <span className="so-mark">{info.app_name[0]}</span>}
      <div className="so-brand"><strong>{info.app_name}</strong><span>{info.outlet_name}{info.table ? `, table ${info.table}` : ""}</span></div>
      {kiosk && step !== "menu" && step !== "status" && <button className="so-restart" onClick={reset}>Start over</button>}
    </header>
  );
  const catTint = (id: string) => info.menu.categories.find((c) => c.id === id)?.color || null;
  const inCart = (itemId: string) => cart.filter((l) => l.item.item_id === itemId).reduce((n, l) => n + l.qty, 0);

  if (step === "status" || step === "pay") {
    const s = status;
    const waitingPay = s?.status === "pending" && s.payment_status === "awaiting";
    return (
      <div className={`so ${kiosk ? "kiosk" : ""}`}>
        {header}
        <div className="so-status">
          {waitingPay ? (
            <>
              <h1>Pay {formatPaise(s.total_paise)}</h1>
              {s.upi_link ? <><QrImage text={s.upi_link} size={kiosk ? 300 : 220} label="Pay by UPI" /><p>Scan with any UPI app (GPay, PhonePe, Paytm…)</p></> : <p>Please pay at the counter.</p>}
              <p className="so-code">Order code <strong>{s.pay_ref}</strong></p>
              <p className="muted">Your order goes to the kitchen as soon as the payment is confirmed.</p>
            </>
          ) : s?.token_no ? (
            <>
              <p className="muted">Your token</p>
              <div className="so-token">{s.token_no}</div>
              <h2>{s.message}</h2>
              {kiosk && <p className="muted">Watch the screen for your number.</p>}
            </>
          ) : (
            <h2>{s?.message || "Sending your order…"}</h2>
          )}
          {(kiosk || s?.status === "completed" || s?.status === "cancelled") && <Button onClick={reset}>{kiosk ? "Done" : "Order again"}</Button>}
        </div>
      </div>
    );
  }

  if (step === "details") {
    return (
      <div className={`so ${kiosk ? "kiosk" : ""}`}>
        {header}
        <div className="so-details">
          <h2>Almost done</h2>
          {(kiosk || info.table) && (
            <div className="tabs">
              <button className={orderType === "dine_in" ? "active" : ""} onClick={() => setOrderType("dine_in")}>{info.table ? `At table ${info.table}` : "Eat here"}</button>
              <button className={orderType === "takeaway" ? "active" : ""} onClick={() => setOrderType("takeaway")}>Takeaway</button>
            </div>
          )}
          <Input placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)} />
          <Input inputMode="tel" placeholder="Mobile number" value={phone} onChange={(e) => setPhone(e.target.value)} />
          {orderType === "takeaway" && <Input placeholder={info.car_number_required ? "Car number (required)" : "Car number (if you're in a car)"} value={car} onChange={(e) => setCar(e.target.value.toUpperCase())} />}
          <div className="so-total">
            <span>{count} item{count === 1 ? "" : "s"}</span>
            <strong>{quote ? `${formatPaise(quote.total_paise)} incl. GST` : formatPaise(subtotal)}</strong>
          </div>
          <ErrorText error={error} />
          {kiosk ? (
            <div className="so-pay">
              {info.upi_available && <Button disabled={busy || !name || !phone} onClick={() => place("upi")}>Pay by UPI</Button>}
              <Button variant="ghost" disabled={busy || !name || !phone} onClick={() => place("counter")}>Pay at counter</Button>
            </div>
          ) : (
            <Button disabled={busy || !name || !phone} onClick={() => place()}>Place order</Button>
          )}
          <button className="link" onClick={() => setStep("menu")}>Back to menu</button>
        </div>
      </div>
    );
  }

  return (
    <div className={`so ${kiosk ? "kiosk" : ""}`}>
      {header}
      {step === "menu" && (
        <section className="so-hero">
          <h1>{kiosk ? "Order here, skip the queue" : info.table ? `Order for table ${info.table}` : "Order ahead"}</h1>
          <p>{kiosk ? "Pick your items, pay by UPI or at the counter, and watch the screen for your token." : "Your order goes to the kitchen once the staff confirm it."}</p>
        </section>
      )}
      <nav className="so-cats">
        {info.menu.categories.map((c) => <button key={c.id} className={cat === c.id ? "active" : ""} onClick={() => setCat(c.id)}>{c.name}</button>)}
      </nav>
      <div className="so-items">
        {items.map((i) => {
          const n = inCart(i.item_id);
          const simple = i.variants.length === 1 && !i.addon_groups.length;
          const line = simple ? cart.find((l) => l.item.item_id === i.item_id) : undefined;
          const from = Math.min(...i.variants.map((v) => v.price_paise));
          return (
            <article key={i.item_id} className={`so-card ${n ? "picked" : ""}`}>
              <ItemArt name={i.name} image={i.image_url} tint={catTint(i.category_id)} size={kiosk ? "lg" : "md"} />
              <div className="so-card-body">
                <h3><FoodDot type={i.food_type} /> {i.name}</h3>
                {i.description && <p className="so-desc">{i.description}</p>}
                <div className="so-card-foot">
                  <span className="so-price">{i.variants.length > 1 ? `From ${formatPaise(from)}` : formatPaise(from)}</span>
                  {line ? (
                    <span className="qty-ctl big">
                      <button onClick={() => setQty(line.key, line.qty - 1)} aria-label={`One less ${i.name}`}>−</button>
                      <span>{line.qty}</span>
                      <button onClick={() => setQty(line.key, line.qty + 1)} aria-label={`One more ${i.name}`}>+</button>
                    </span>
                  ) : (
                    <button className="so-add" onClick={() => (simple ? add(i, i.variants[0]) : setPicking(i))}>{n ? `Add more (${n})` : "Add"}</button>
                  )}
                </div>
              </div>
            </article>
          );
        })}
      </div>
      {cart.length > 0 && (
        <div className="so-cart">
          <ul>
            {cart.map((l) => (
              <li key={l.key}>
                <span className="grow">{l.item.name}{l.variant.name !== "Regular" ? ` (${l.variant.name.replace(/\bML\b/, "ml")})` : ""}</span>
                <span className="qty-ctl"><button onClick={() => setQty(l.key, l.qty - 1)} aria-label="One less">−</button><span>{l.qty}</span><button onClick={() => setQty(l.key, l.qty + 1)} aria-label="One more">+</button></span>
                <span className="price">{formatPaise(unit(l) * l.qty)}</span>
              </li>
            ))}
          </ul>
          <ErrorText error={error} />
          <Button className="btn-block" onClick={toDetails}>Review order: {count} item{count === 1 ? "" : "s"}, {formatPaise(subtotal)}</Button>
        </div>
      )}
      {picking && <Picker item={picking} onClose={() => setPicking(null)} onPick={(v, a) => { add(picking, v, a); setPicking(null); }} />}
      {info.show_powered_by && <footer className="powered">Powered by Wise</footer>}
    </div>
  );
}

function Picker({ item, onPick, onClose }: { item: OutletMenuItem; onClose: () => void; onPick: (v: OutletMenuVariant, a: string[]) => void }) {
  const [variant, setVariant] = useState(item.variants[0]);
  const [addons, setAddons] = useState<string[]>([]);
  const ok = item.addon_groups.every((g) => g.addons.filter((a) => a.id && addons.includes(a.id)).length >= g.min_select);
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{item.name}</h2>
        <div className="chips">
          {item.variants.map((v) => <button key={v.variant_id} className={`choice big ${v.variant_id === variant.variant_id ? "on" : ""}`} onClick={() => setVariant(v)}>{v.name} · {formatPaise(v.price_paise)}</button>)}
        </div>
        {item.addon_groups.map((g) => {
          const ids = g.addons.map((a) => a.id!).filter(Boolean);
          return (
            <div key={g.id}>
              <h3>{g.name} <span className="muted small">{g.min_select ? `choose ${g.min_select}–${g.max_select}` : `up to ${g.max_select}`}</span></h3>
              <div className="chips">
                {g.addons.filter((a) => a.is_active).map((a) => (
                  <button key={a.id} className={`choice big ${a.id && addons.includes(a.id) ? "on" : ""}`} onClick={() => a.id && setAddons((cur) => {
                    if (cur.includes(a.id!)) return cur.filter((x) => x !== a.id);
                    const inGroup = cur.filter((x) => ids.includes(x));
                    if (g.max_select === 1) return [...cur.filter((x) => !ids.includes(x)), a.id!];
                    return inGroup.length >= g.max_select ? cur : [...cur, a.id!];
                  })}>{a.name}{a.price_paise ? ` +${formatPaise(a.price_paise)}` : ""}</button>
                ))}
              </div>
            </div>
          );
        })}
        <div className="row">
          <Button disabled={!ok} onClick={() => onPick(variant, addons)}>Add to order</Button>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </div>
  );
}
