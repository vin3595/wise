import { useEffect, useMemo, useRef, useState } from "react";
import { api, newId } from "../api";
import { billOffline, goOffline, isOffline, prepare, quoteOffline, rememberMenu, rememberOutlets, savedMenu, savedOutlets, type OfflineResult } from "../offline";
import { OfflineBar, PrintOffline, useOffline } from "../components/Offline";
import { ORDER_TYPE_LABEL, usePolling } from "../hooks";
import { formatPaise, inputToPaise } from "../money";
import { QrImage } from "../qr";
import { useSession } from "../session";
import type { Bill, BillPreview, Order, OrderType, Outlet, OutletMenu, OutletMenuItem, OutletMenuVariant } from "../types";
import { Button, ErrorText, FoodDot, Input, Select, useAction } from "../ui";
import BillDialog from "../components/BillDialog";
import ShiftBar from "../components/ShiftBar";
import PendingOrders from "../components/PendingOrders";
import ApprovalDialog from "../components/ApprovalDialog";
import { PrintBill } from "../components/Receipt";
import UpiAuto, { UpiWatcher, type UpiReq } from "../components/UpiAuto";
import SplitMove from "../components/SplitMove";
import { cleanMobile, openCustomerScreen, useCounterScreen, type ScreenState } from "../counterScreen";
import CardAuto, { type CardReq } from "../components/CardAuto";
import { LoyaltyBox, type LoyaltyUse } from "../components/Loyalty";

interface CartLine {
  key: string;
  item: OutletMenuItem;
  variant: OutletMenuVariant;
  addonIds: string[];
  qty: number;
}

const unitPrice = (l: CartLine) =>
  l.variant.price_paise + l.item.addon_groups.flatMap((g) => g.addons).filter((a) => a.id && l.addonIds.includes(a.id))
    .reduce((s, a) => s + a.price_paise, 0);
const short = (v: string) => v.replace(/\s*ML$/i, " ml").replace(/^Regular$/, "");
type PayMode = "cash" | "upi" | "card";

/** Counter / captain screen: take an order, send it to the kitchen, and (for cashiers) settle it in one go. */
export default function Orders() {
  const { me, deviceOutletId, can, branding } = useSession();
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [outletId, setOutletId] = useState(me?.outlet_id || deviceOutletId || "");
  const [menu, setMenu] = useState<OutletMenu | null>(null);
  const [cat, setCat] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [cart, setCart] = useState<CartLine[]>([]);
  const [orderType, setOrderType] = useState<OrderType>("takeaway");
  const [table, setTable] = useState("");
  const [car, setCar] = useState("");
  const [customer, setCustomer] = useState("");
  const [notes, setNotes] = useState("");
  const [phone, setPhone] = useState("");
  const [declined, setDeclined] = useState(false);
  const [quote, setQuote] = useState<number | null>(null);
  const [quoteFailed, setQuoteFailed] = useState(false);
  const [quoteTry, setQuoteTry] = useState(0);
  const [payMode, setPayMode] = useState<PayMode | null>(null);
  const [tendered, setTendered] = useState("");
  const [addingTo, setAddingTo] = useState<Order | null>(null);
  const [picking, setPicking] = useState<OutletMenuItem | null>(null);
  const [sent, setSent] = useState<Order | null>(null);
  const [billing, setBilling] = useState<Order | null>(null);
  const [printing, setPrinting] = useState<Bill | null>(null);
  const [offlinePrint, setOfflinePrint] = useState<OfflineResult | null>(null);
  const [offlineDone, setOfflineDone] = useState<Bill | null>(null);
  const off = useOffline();
  const rewardOf = useRef(0);
  const [upiAuto, setUpiAuto] = useState<{ enabled: boolean; provider: string | null; card_enabled?: boolean }>({ enabled: false, provider: null });
  const [cardReq, setCardReq] = useState<CardReq | null>(null);
  const [terminals, setTerminals] = useState<{ id: string; name: string; is_active: boolean }[]>([]);
  const [pickTerminal, setPickTerminal] = useState<Order | null>(null);
  const lastCard = useRef<{ customer: { name: string | null; phone: string | null; declined: boolean }; loyalty: LoyaltyUse | null }>({ customer: { name: null, phone: null, declined: true }, loyalty: null });
  const termKey = `wise.card_terminal.${outletId}`;
  const myTerminal = () => { try { const t = localStorage.getItem(termKey); return terminals.find((x) => x.id === t && x.is_active) ? t : null; } catch { return null; } };
  const [upiReq, setUpiReq] = useState<UpiReq | null>(null);
  const [loy, setLoy] = useState<LoyaltyUse>({ points_paise: 0, use_reward: false });
  const [loyAmount, setLoyAmount] = useState(0);   // paise off for the points/reward chosen
  const loyalty = loy.points_paise || loy.use_reward ? loy : null;
  const [refresh, setRefresh] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const { busy, error, setError, run } = useAction();

  const outlet = outlets.find((o) => o.id === outletId);
  // The outlet decides which order types the counter takes and which one each new order starts on.
  const allowedTypes: OrderType[] = outlet?.order_types?.length ? outlet.order_types : ["dine_in", "takeaway", "delivery"];
  const startType: OrderType = outlet && allowedTypes.includes(outlet.default_order_type) ? outlet.default_order_type : allowedTypes.includes("takeaway") ? "takeaway" : allowedTypes[0];
  useEffect(() => { setOrderType(startType); }, [outletId, startType]);
  const catColor = (id: string) => menu?.categories.find((c) => c.id === id)?.color || null;
  const canBill = can("billing") && !addingTo;

  useEffect(() => {
    api<Outlet[]>("/tenant/outlets").then((o) => { rememberOutlets(o); return o; })
      .catch((e) => { if (!isOffline(e)) throw e; goOffline(); return savedOutlets(); })
      .then((o) => { setOutlets(o); setOutletId((c) => c || o[0]?.id || ""); });
  }, []);
  // "Sold out" mode: tapping a tile marks it sold out for today (or back on), for every counter, kiosk and QR menu.
  const [soldMode, setSoldMode] = useState(false);
  const toggleSold = async (item: OutletMenuItem, v: OutletMenuVariant) => {
    try {
      await api(`/menu/outlets/${outletId}/sold-out`, { body: { variant_ids: [v.variant_id], sold_out: !v.sold_out } });
      setMenu((m) => m && { ...m, items: m.items.map((i) => i.item_id !== item.item_id ? i : { ...i, variants: i.variants.map((x) => x.variant_id === v.variant_id ? { ...x, sold_out: !v.sold_out } : x) }) });
    } catch (e) { setError(e instanceof Error ? e.message : "Couldn't change it"); }
  };
  usePolling(async () => {
    if (!outletId || !off.online) return;
    const m = await api<OutletMenu>(`/menu/outlets/${outletId}`);
    rememberMenu(outletId, m);
    setMenu((cur) => (cur ? { ...m } : cur));
  }, 300000, [outletId, off.online], { outletId, topics: ["menu"] });
  useEffect(() => {
    if (!outletId) return;
    api<OutletMenu>(`/menu/outlets/${outletId}`).then((m) => { rememberMenu(outletId, m); return m; })
      .catch((e) => { const m = savedMenu(outletId); if (!isOffline(e) || !m) throw e; goOffline(); return m; })
      .then((m) => { setMenu(m); setCat(m.categories[0]?.id ?? null); });
    // Keep this counter's offline pack (own bill series, GST rates, bill header) up to date.
    if (can("billing")) void prepare(outletId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outletId]);
  useEffect(() => { if (can("billing")) api<{ enabled: boolean; provider: string | null; card_enabled?: boolean }>("/upi/enabled").then(setUpiAuto).catch(() => {}); }, [can]);
  useEffect(() => { if (upiAuto.card_enabled && outletId) api<typeof terminals>(`/outlets/${outletId}/card-terminals`).then(setTerminals).catch(() => setTerminals([])); }, [upiAuto.card_enabled, outletId]);
  // "/" jumps to search from anywhere on the screen.
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) { e.preventDefault(); searchRef.current?.focus(); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);

  const lines = () => cart.map((l) => ({ variant_id: l.variant.variant_id, qty: l.qty, addon_ids: l.addonIds }));
  // Total with GST for the pay buttons, refreshed as the cart changes.
  useEffect(() => {
    setPayMode(null);
    if (!cart.length || !outletId) { setQuote(null); return; }
    if (!off.online) { setQuote(quoteOffline(cart)); return; }
    setQuote(null); setQuoteFailed(false);
    const t = setTimeout(() => {
      api<{ total_paise: number }>("/orders/quote", { body: { outlet_id: outletId, items: lines(), discount_paise: loyAmount } })
        .then((q) => setQuote(q.total_paise))
        .catch((e) => { if (isOffline(e)) { goOffline(); setQuote(quoteOffline(cart)); } else { setQuote(null); setQuoteFailed(true); } });
    }, 150);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cart, outletId, off.online, loyAmount, quoteTry]);

  const shown = useMemo(() => {
    if (!menu) return [];
    const q = search.trim().toLowerCase();
    if (!q) return menu.items.filter((i) => i.category_id === cat);
    const code = menu.items.filter((i) => (i.short_code || "").toLowerCase() === q);
    return [...code, ...menu.items.filter((i) => !code.includes(i) && (i.name.toLowerCase().includes(q) || (i.short_code || "").toLowerCase().startsWith(q)))];
  }, [menu, cat, search]);

  function addLine(item: OutletMenuItem, variant: OutletMenuVariant, addonIds: string[] = []) {
    if (soldMode) { void toggleSold(item, variant); return; }
    if (variant.sold_out) { setError(`${item.name}${item.variants.length > 1 ? ` (${variant.name})` : ""} is sold out today. Tap "Sold out" to bring it back.`); return; }
    const key = `${variant.variant_id}|${[...addonIds].sort().join(",")}`;
    setSent(null);
    setOfflineDone(null);
    setCart((c) => {
      const hit = c.find((l) => l.key === key);
      return hit ? c.map((l) => (l.key === key ? { ...l, qty: l.qty + 1 } : l)) : [...c, { key, item, variant, addonIds, qty: 1 }];
    });
  }
  const setQty = (key: string, qty: number) => setCart((c) => (qty <= 0 ? c.filter((l) => l.key !== key) : c.map((l) => (l.key === key ? { ...l, qty } : l))));
  const subtotal = cart.reduce((s, l) => s + unitPrice(l) * l.qty, 0);
  const count = cart.reduce((s, l) => s + l.qty, 0);

  function resetCart() {
    setCart([]); setTable(""); setCar(""); setCustomer(""); setNotes(""); setPhone(""); setDeclined(false); setOrderType(startType);
    setAddingTo(null); setPayMode(null); setTendered(""); setRefresh((n) => n + 1); setLoy({ points_paise: 0, use_reward: false }); setLoyAmount(0);
  }

  async function placeOrder(id = newId()): Promise<Order> {
    return addingTo
      ? api<Order>(`/orders/${addingTo.id}/items`, { body: { items: lines() } })
      : api<Order>("/orders", { body: { id, outlet_id: outletId, order_type: orderType, table_label: table || null,
          car_number: car || null, customer_name: customer || null, notes: notes || null, items: lines() } });
  }

  function send(thenBill = false) {
    run(async () => {
      const order = await placeOrder();
      setSent(order);
      resetCart();
      if (thenBill) setBilling(order);
    });
  }

  /** One tap: send to the kitchen and settle the whole bill in cash / UPI / card. */
  /** The box didn't answer: bill on this counter, print the bill and kitchen slips here, upload later. */
  function settleOffline(mode: PayMode, triedOrderId: string | null) {
    const r = billOffline({ cart, madeBy: { id: me!.user.id, name: me!.user.name }, orderType, table: orderType === "dine_in" ? table || null : null,
      car: orderType === "takeaway" ? car || null : null, customerName: customer || null, notes: notes || null, phone: phone || null,
      declined, mode, triedOrderId });
    resetCart();
    setOfflinePrint(r);
  }

  function settle(mode: PayMode) {
    if (orderType === "dine_in" && !table && !off.online) { setError("Pick a table for dine-in orders"); return; }
    if (!off.online) { run(async () => settleOffline(mode, null)); return; }
    run(async () => {
      const orderId = newId();
      let order: Order;
      try {
        order = await placeOrder(orderId);
      } catch (e) {
        if (!isOffline(e)) throw e;
        goOffline();
        settleOffline(mode, orderId);
        return;
      }
      setSent(order);
      const pv = await api<BillPreview>("/bills/preview", { body: { order_id: order.id, loyalty: loyalty ? { ...loyalty, phone } : null } });
      try {
        const bill = await api<Bill>("/bills", { body: { id: newId(), order_id: order.id, loyalty,
          customer: { name: customer || null, phone: declined ? null : phone, declined },
          payments: [{ mode, amount_paise: pv.total_paise, reference: null }] } });
        resetCart();
        setPrinting(bill);
        setRefresh((n) => n + 1);
      } catch (e) {
        resetCart();
        setBilling(order); // the order reached the kitchen; finish the bill in the full dialog
        throw e;
      }
    });
  }
  /** UPI that confirms itself: send the order, show this bill's own QR, the bill prints when the money lands. */
  function upiAutoPay() {
    run(async () => {
      const order = await placeOrder();
      setSent(order);
      try {
        const req = await api<UpiReq>("/upi/requests", { body: { order_id: order.id, bill_id: newId(), loyalty, customer: { name: customer || null, phone: declined ? null : phone, declined } } });
        resetCart();
        setUpiReq(req);
      } catch (e) {
        resetCart();
        setBilling(order); // the kitchen has it; take UPI on the outlet QR in the full dialog
        throw e;
      }
    });
  }
  /** Card machine linked: send the order, push the amount to this counter's machine, bill when approved. */
  function cardAutoPay(existing?: Order) {
    run(async () => {
      const order = existing ?? (await placeOrder());
      if (!existing) setSent(order);
      if (!existing) lastCard.current = { customer: { name: customer || null, phone: declined ? null : phone, declined }, loyalty };
      const tid = myTerminal();
      if (!tid) { if (!existing) resetCart(); setPickTerminal(order); return; }
      try {
        const req = await api<CardReq>("/card/requests", { body: { order_id: order.id, bill_id: newId(), terminal_id: tid, ...lastCard.current } });
        if (!existing) resetCart();
        setCardReq(req);
      } catch (e) {
        if (!existing) resetCart();
        setBilling(order);
        throw e;
      }
    });
  }
  const billed = (b: Bill) => { setBilling(null); setPrinting(b); setRefresh((n) => n + 1); };

  const total = quote ?? subtotal;
  const given = inputToPaise(tendered || "0") ?? 0;
  const presets = [...new Set([total, Math.ceil(total / 10000) * 10000, 50000, 100000, 200000].filter((p) => p >= total))].slice(0, 4);
  const upiLink = outlet?.upi_vpa && total > 0
    ? `upi://pay?pa=${encodeURIComponent(outlet.upi_vpa)}&pn=${encodeURIComponent(outlet.upi_payee_name || outlet.name)}&am=${(total / 100).toFixed(2)}&cu=INR`
    : null;

  // The customer screen (second monitor): shows the cart and the QR, and asks the customer for their number.
  const [thanks, setThanks] = useState<ScreenState["thanks"]>(null);
  useEffect(() => {
    if (!printing) return;
    setThanks({ amount_paise: printing.total_paise, token_no: printing.token_no, sms: !!printing.customer_phone });
    const t = setTimeout(() => setThanks(null), 7000);
    return () => clearTimeout(t);
  }, [printing]);
  // The keypad shows on the customer screen only when the cashier asks; otherwise the screen can't be touched.
  const [asking, setAsking] = useState(false);
  const [ads, setAds] = useState<ScreenState["ads"]>([]);
  useEffect(() => { if (!cart.length || declined || cleanMobile(phone)) setAsking(false); }, [cart.length, declined, phone]);
  const screenState: ScreenState = {
    brand: { app_name: branding?.app_name || "Welcome", logo_url: branding?.logo_url || null, primary_color: branding?.primary_color || "#2563eb", outlet: outlet?.name || "" },
    // While a UPI QR is out the cart is already cleared: show the bill it is for.
    lines: cart.length || !upiReq?.slip ? cart.map((l) => ({ name: l.item.name + (short(l.variant.name) ? ` (${short(l.variant.name)})` : ""), qty: l.qty, amount_paise: unitPrice(l) * l.qty }))
      : upiReq.slip.lines.map((l) => ({ name: l.item_name + (short(l.variant_name) ? ` (${short(l.variant_name)})` : ""), qty: l.qty, amount_paise: l.gross_paise })),
    total_paise: cart.length ? total : upiReq?.slip ? upiReq.slip.total_paise : 0,
    discount_paise: cart.length ? loyAmount : upiReq?.slip ? upiReq.slip.discount_paise : 0,
    ask: asking && canBill && cart.length > 0 && !declined && !upiReq && payMode === null,
    phone: cleanMobile(phone) || "", name: customer,
    qr: upiReq && upiReq.status === "waiting" && upiReq.upi_string ? { upi: upiReq.upi_string, amount_paise: upiReq.amount_paise }
      : payMode === "upi" && upiLink && cart.length ? { upi: upiLink, amount_paise: total } : null,
    thanks: cart.length ? null : thanks,
    ads,
  };
  const screenOn = useCounterScreen(screenState, (m) => {
    if (m.t === "customer") { setPhone(m.phone); if (m.name) setCustomer(m.name); setDeclined(false); setError(null); setAsking(false); }
    else if (m.t === "skip") { setDeclined(true); setAsking(false); }
    else if (m.t === "timeout") { setAsking(false); setError("The customer didn't enter a number. Ask again, type it, or tap No number."); }
  });
  // Ads for this outlet's customer screen: fetched when a screen is connected, refreshed every 10 minutes.
  useEffect(() => {
    if (!screenOn || !outletId || !off.online) return;
    let live = true;
    const load = () => api<ScreenState["ads"]>(`/outlets/${outletId}/ads`).then((a) => { if (live) setAds(a); }).catch(() => undefined);
    load();
    const t = setInterval(load, 600000);
    return () => { live = false; clearInterval(t); };
  }, [screenOn, outletId, off.online]);

  return (
    <>
    {can("billing") && <OfflineBar />}
    {outletId && can("billing") && off.online && <ShiftBar key={`${outletId}-${refresh}`} outletId={outletId} />}
    <div className="pos">
      <div className="pos-menu">
        {outletId && off.online && <PendingOrders outletId={outletId} onBilled={billed} />}
        <div className="pos-search">
          {!me?.outlet_id && outlets.length > 1 && (
            <Select value={outletId} onChange={(e) => { setOutletId(e.target.value); setCart([]); }}>
              {outlets.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </Select>
          )}
          <input ref={searchRef} className="input search" placeholder="Search item or short code, Enter adds it" value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && shown[0]) { addLine(shown[0], shown[0].variants[0]); setSearch(""); }
              if (e.key === "Escape") setSearch("");
            }} />
          <kbd className="hint">/</kbd>
          {off.online && <button className={`choice sold-toggle ${soldMode ? "on" : ""}`} aria-pressed={soldMode} onClick={() => setSoldMode(!soldMode)}
            title="Tap items to mark them sold out for today">{soldMode ? "Done" : "Sold out"}</button>}
        </div>
        {soldMode && <p className="sold-hint">Tap an item (or size) to mark it sold out for today. Tap again to bring it back. Kiosk and QR menu update at once.</p>}
        <div className="pos-cats">
          {menu?.categories.map((c) => (
            <button key={c.id} className={!search && cat === c.id ? "active" : ""} style={{ ["--cat" as string]: c.color || "var(--brand)" }}
              onClick={() => { setCat(c.id); setSearch(""); }}>{c.name}</button>
          ))}
        </div>
        <div className="pos-grid">
          {shown.map((i) => {
            const multi = i.variants.length > 1;
            const mine = cart.filter((l) => l.item.item_id === i.item_id);
            const inCart = mine.reduce((s, l) => s + l.qty, 0);
            const last = mine[mine.length - 1];
            return (
              <div key={i.item_id} className={`tile ${i.variants.every((v) => v.sold_out) ? "sold" : ""} ${soldMode ? "sold-mode" : ""} ${inCart ? "in-cart" : ""}`} style={{ ["--cat" as string]: catColor(i.category_id) || "var(--brand)" }}>
                <button className="tile-main" onClick={() => addLine(i, i.variants[0])}>
                  {i.variants.every((v) => v.sold_out) && <span className="sold-tag">Sold out</span>}
                  <span className="tile-name"><FoodDot type={i.food_type} /> {i.name}</span>
                  {!multi && <span className="tile-price">{formatPaise(i.variants[0].price_paise)}</span>}
                  {i.short_code && <span className="tile-code">{i.short_code}</span>}
                </button>
                {multi && (
                  <div className="tile-sizes">
                    {i.variants.map((v) => (
                      <button key={v.variant_id} className={v.sold_out ? "sold" : ""} onClick={() => addLine(i, v)} aria-label={v.sold_out ? `${i.name} ${v.name}, sold out` : undefined}>
                        <span>{short(v.name)}</span><strong>{v.sold_out ? "Sold out" : formatPaise(v.price_paise)}</strong>
                      </button>
                    ))}
                  </div>
                )}
                {i.addon_groups.length > 0 && <button className="tile-addons" onClick={() => setPicking(i)}>+ Add-ons</button>}
                {inCart > 0 && !soldMode && (
                  <div className="tile-step">
                    <button onClick={() => setQty(last.key, last.qty - 1)} aria-label={`One less ${i.name}`}>−</button>
                    <span>{inCart}</span>
                    <button onClick={() => addLine(last.item, last.variant, last.addonIds)} aria-label={`One more ${i.name}`}>+</button>
                  </div>
                )}
              </div>
            );
          })}
          {menu && shown.length === 0 && <p className="muted">{search ? "No item matches that." : "No items here yet. Add them under Menu."}</p>}
        </div>
      </div>

      {cart.length > 0 && (
        <button className="cart-jump" onClick={() => document.querySelector(".pos-cart")?.scrollIntoView({ behavior: "smooth", block: "start" })}>
          <span>{count} item{count === 1 ? "" : "s"}</span><strong>{formatPaise(total)}</strong><span>View bill ↓</span>
        </button>
      )}
      <aside className="pos-cart">
        {addingTo ? (
          <div className="banner">Adding to token #{addingTo.token_no}{addingTo.table_label ? `, table ${addingTo.table_label}` : ""} <button className="link" onClick={() => setAddingTo(null)}>Cancel</button></div>
        ) : (
          <>
            {allowedTypes.length > 1 && <div className="seg">
              {allowedTypes.map((t) => (
                <button key={t} className={orderType === t ? "active" : ""} onClick={() => setOrderType(t)}>{ORDER_TYPE_LABEL[t]}</button>
              ))}
            </div>}
            {allowedTypes.length === 1 && <p className="small muted only-type">{ORDER_TYPE_LABEL[allowedTypes[0]]} orders</p>}
            <div className="cart-meta">
              {orderType === "dine_in" && <Input placeholder="Table, e.g. T4" value={table} onChange={(e) => setTable(e.target.value.toUpperCase())} />}
              {orderType === "takeaway" && (
                <Input placeholder={outlet?.car_number_required ? "Car number (required)" : "Car number"} value={car}
                  onChange={(e) => setCar(e.target.value.toUpperCase())} />
              )}
              <Input placeholder="Customer name" value={customer} onChange={(e) => setCustomer(e.target.value)} />
            </div>
          </>
        )}
        <ul className="cart-lines">
          {cart.map((l) => (
            <li key={l.key}>
              <div className="grow">
                <span className="cart-name">{l.item.name}</span>{short(l.variant.name) && <span className="cart-size"> {short(l.variant.name)}</span>}
                {l.addonIds.length > 0 && <div className="muted small">+ {l.item.addon_groups.flatMap((g) => g.addons).filter((a) => a.id && l.addonIds.includes(a.id)).map((a) => a.name).join(", ")}</div>}
              </div>
              <div className="qty-ctl">
                <button onClick={() => setQty(l.key, l.qty - 1)} aria-label={`One less ${l.item.name}`}>−</button>
                <span>{l.qty}</span>
                <button onClick={() => setQty(l.key, l.qty + 1)} aria-label={`One more ${l.item.name}`}>+</button>
              </div>
              <span className="price">{formatPaise(unitPrice(l) * l.qty)}</span>
            </li>
          ))}
          {cart.length === 0 && <li className="cart-empty">Tap an item or a size to add it.</li>}
        </ul>
        {cart.length > 0 && !addingTo && <Input placeholder="Note for the kitchen" value={notes} onChange={(e) => setNotes(e.target.value)} />}
        <div className="cart-total">
          <span>{count ? `${count} item${count === 1 ? "" : "s"}` : "Total"}{count > 0 && <small>{quote !== null ? " incl. GST" : " + GST"}</small>}</span>
          <strong className={count > 0 && quote === null ? "pending-total" : ""}>{formatPaise(total)}</strong>
        </div>

        {canBill && cart.length > 0 && (
          <div className="quickpay">
            <div className="cust-row">
              <Input inputMode="tel" placeholder="Customer mobile (optional)" aria-label="Customer mobile, optional" value={phone} onChange={(e) => setPhone(e.target.value)} />
            </div>
            {screenOn
              ? (!cleanMobile(phone) && (asking
                  ? <p className="muted small cs-status"><span className="dot-pulse" aria-hidden /> Customer is typing on their screen… <button className="link" onClick={() => setAsking(false)}>Stop</button></p>
                  : <>
                      {declined && <p className="muted small cs-status">Customer skipped the number.</p>}
                      <button className="choice cs-ask" onClick={() => { setError(null); setDeclined(false); setAsking(true); }}>{declined ? "Ask again on their screen" : "Ask customer on their screen"}</button>
                    </>))
              : <button className="link small" onClick={openCustomerScreen}>Open customer screen</button>}
            {!declined && off.online && <LoyaltyBox phone={phone} gross={subtotal} value={loy} onInfo={(l) => { rewardOf.current = l ? l.reward_paise : 0; }} onChange={(u) => {
              setLoy(u);
              // the reward is worth at most the bill; the server checks again when billing
              setLoyAmount(u.points_paise + (u.use_reward ? Math.min(rewardOf.current, subtotal) : 0));
            }} />}
            {payMode === null && (
              <p className="small pay-wait" role="status">
                {quote !== null ? null : quoteFailed
                  ? <>Couldn't work out the total with GST. <button className="link" onClick={() => setQuoteTry((n) => n + 1)}>Try again</button></>
                  : "Adding GST to the total…"}
              </p>
            )}
            {payMode === null ? (
              <div className="pay-buttons">
                <button className="pay pay-cash" disabled={busy || quote === null} onClick={() => setPayMode("cash")}>Cash</button>
                <button className="pay pay-upi" disabled={busy || quote === null} onClick={() => (upiAuto.enabled && off.online ? upiAutoPay() : setPayMode("upi"))}>UPI</button>
                <button className="pay pay-card" disabled={busy || quote === null} onClick={() => (upiAuto.card_enabled && off.online && terminals.length ? cardAutoPay() : settle("card"))}>Card</button>
              </div>
            ) : payMode === "cash" ? (
              <div className="tender">
                <div className="presets">
                  {presets.map((p) => <button key={p} className={given === p ? "on" : ""} onClick={() => setTendered(String(p / 100))}>{p === total ? "Exact" : formatPaise(p)}</button>)}
                </div>
                <Input inputMode="decimal" placeholder="Cash received ₹" value={tendered} onChange={(e) => setTendered(e.target.value)} autoFocus />
                {given > 0 && <p className={given >= total ? "change" : "error"}>{given >= total ? <>Return <strong>{formatPaise(given - total)}</strong></> : `Short by ${formatPaise(total - given)}`}</p>}
                <div className="row">
                  <Button className="grow" disabled={busy || (given > 0 && given < total)} onClick={() => settle("cash")}>Settle {formatPaise(total)} cash</Button>
                  <button className="link" onClick={() => setPayMode(null)}>Back</button>
                </div>
              </div>
            ) : (
              <div className="tender upi">
                {upiLink ? <QrImage text={upiLink} size={168} label="UPI QR for this bill" /> : <p className="muted">Add the outlet's UPI ID under Outlets to show a QR here.</p>}
                <div className="row">
                  <Button className="grow" disabled={busy} onClick={() => settle("upi")}>UPI received {formatPaise(total)}</Button>
                  <button className="link" onClick={() => setPayMode(null)}>Back</button>
                </div>
              </div>
            )}
          </div>
        )}
        <ErrorText error={error} />
        <div className="send-row">
          {!off.online && <p className="muted small grow">{canBill ? "Box not reachable: only Cash, UPI and Card bills for now. Discounts, split bills and pay-later come back when it answers." : "Box not reachable. Take the order on paper or send it to the billing counter."}</p>}
          {off.online && <>
          <Button variant={canBill && cart.length ? "ghost" : "primary"} className="grow" disabled={busy || cart.length === 0 || !outletId} onClick={() => send(false)}>
            {addingTo ? "Send added items" : canBill ? "Send, pay later" : "Send to kitchen"}
          </Button>
          {canBill && <Button variant="ghost" disabled={busy || cart.length === 0 || !outletId} onClick={() => send(true)} title="Sends the order to the kitchen and opens the bill for discount, split or other payments">Send & open bill</Button>}
          </>}
        </div>
        {offlineDone && cart.length === 0 && (
          <div className="sent">Bill <strong>{offlineDone.invoice_no}</strong>, token <strong>#{offlineDone.token_no}</strong> printed with its kitchen slips. Hand the slips to the kitchen.</div>
        )}
        {sent && (
          <div className="sent">
            {sent.merged_into_existing ? <>Added to table {sent.table_label}'s open order, token <strong>#{sent.token_no}</strong></> :
              <>Token <strong>#{sent.token_no}</strong> sent to {sent.kots.filter((k) => k.status === "new").map((k) => k.prep_area_name).join(" and ")}</>}
          </div>
        )}
        {outletId && off.online && <ActiveOrders key={refresh} outletId={outletId} onAdd={(o) => { setAddingTo(o); setSent(null); }}
          onBill={can("billing") ? setBilling : undefined} tables={outlet?.table_labels || []} />}
      </aside>

      {picking && <VariantPicker item={picking} onClose={() => setPicking(null)} onPick={(v, a) => { addLine(picking, v, a); setPicking(null); }} />}
      {billing && <BillDialog order={billing} outlet={outlet} onClose={() => setBilling(null)} onBilled={billed}
        upiAuto={upiAuto.enabled && off.online} onUpiRequest={(r) => { setBilling(null); setUpiReq(r); }} />}
      {printing && <PrintBill bill={printing} onDone={() => setPrinting(null)} />}
      {cardReq && <CardAuto request={cardReq} onBilled={(b) => { setCardReq(null); billed(b); }} onClose={() => { setCardReq(null); setRefresh((n) => n + 1); }}
        onRetry={() => { const o = cardReq.order_id; setCardReq(null); cardAutoPay({ id: o } as Order); }} />}
      {pickTerminal && (
        <div className="modal-back" onClick={() => setPickTerminal(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Which card machine is at this counter?</h2>
            <p className="muted small">Asked once on this device. Change it later by clearing it under Setup &gt; Payments.</p>
            <div className="chips">{terminals.filter((t) => t.is_active).map((t) => <button key={t.id} className="choice big" onClick={() => {
              try { localStorage.setItem(termKey, t.id); } catch { /* */ }
              const o = pickTerminal; setPickTerminal(null); cardAutoPay(o);
            }}>{t.name}</button>)}</div>
          </div>
        </div>
      )}
      {upiReq && <UpiAuto request={upiReq} onBilled={(b) => { setUpiReq(null); billed(b); }} onClose={() => { setUpiReq(null); setRefresh((n) => n + 1); }}
        onLater={() => { setUpiReq(null); setRefresh((n) => n + 1); }} />}
      {upiAuto.enabled && outletId && can("billing") && <UpiWatcher outletId={outletId} hideId={upiReq?.id} onPrint={(b) => setPrinting(b)} />}
      {offlinePrint && <PrintOffline result={offlinePrint} onDone={() => { setOfflineDone(offlinePrint.bill); setOfflinePrint(null); }} />}
    </div>
    </>
  );
}

function VariantPicker({ item, onPick, onClose }: { item: OutletMenuItem; onClose: () => void;
  onPick: (v: OutletMenuVariant, addonIds: string[]) => void }) {
  const [variant, setVariant] = useState(item.variants[0]);
  const [addons, setAddons] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  function toggle(groupMax: number, groupIds: string[], id: string) {
    setAddons((cur) => {
      if (cur.includes(id)) return cur.filter((x) => x !== id);
      const inGroup = cur.filter((x) => groupIds.includes(x));
      const next = groupMax === 1 ? [...cur.filter((x) => !groupIds.includes(x)), id] : inGroup.length >= groupMax ? cur : [...cur, id];
      return next;
    });
  }
  function confirm() {
    for (const g of item.addon_groups) {
      const n = g.addons.filter((a) => a.id && addons.includes(a.id)).length;
      if (n < g.min_select) { setError(`Pick at least ${g.min_select} from ${g.name}`); return; }
    }
    onPick(variant, addons);
  }
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{item.name}</h2>
        <div className="chips">
          {item.variants.map((v) => (
            <button key={v.variant_id} className={`choice ${v.variant_id === variant.variant_id ? "on" : ""}`} onClick={() => setVariant(v)}>
              {v.name} · {formatPaise(v.price_paise)}
            </button>
          ))}
        </div>
        {item.addon_groups.map((g) => {
          const ids = g.addons.map((a) => a.id!).filter(Boolean);
          return (
            <div key={g.id}>
              <h3>{g.name} <span className="muted small">{g.min_select ? `pick ${g.min_select}–${g.max_select}` : `up to ${g.max_select}`}</span></h3>
              <div className="chips">
                {g.addons.filter((a) => a.is_active).map((a) => (
                  <button key={a.id} className={`choice ${a.id && addons.includes(a.id) ? "on" : ""}`} onClick={() => a.id && toggle(g.max_select, ids, a.id)}>
                    {a.name}{a.price_paise ? ` +${formatPaise(a.price_paise)}` : ""}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
        <ErrorText error={error} />
        <div className="row">
          <Button onClick={confirm}>Add</Button>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
        </div>
      </div>
    </div>
  );
}

const STATUS_LABEL = { pending: "Waiting", new: "Sent", preparing: "Preparing", ready: "Ready", completed: "Done", cancelled: "Cancelled" } as const;

function ActiveOrders({ outletId, onAdd, onBill, tables }: { outletId: string; onAdd: (o: Order) => void; onBill?: (o: Order) => void; tables: string[] }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [changing, setChanging] = useState<{ order: Order; mode: "split" | "move" } | null>(null);
  const [q, setQ] = useState("");
  const [cancelling, setCancelling] = useState<Order | null>(null);
  const { error, run } = useAction();
  const query = /^\d{1,3}$/.test(q.trim()) ? `&token=${q.trim()}` : q.trim() ? `&car=${encodeURIComponent(q.trim())}` : "";
  usePolling(async () => setOrders(await api<Order[]>(`/orders?outlet_id=${outletId}${query}`)), 5000, [outletId, query], { outletId, topics: ["orders", "bills"] });
  return (
    <div className="active-orders">
      <h3>Open orders</h3>
      <Input placeholder="Find by token or car number (last 4 digits)" value={q} onChange={(e) => setQ(e.target.value)} />
      <ErrorText error={error} />
      <ul>
        {orders.map((o) => (
          <li key={o.id} className={`order-row st-${o.status}`}>
            <span className="token">#{o.token_no}</span>
            <span className="grow">
              {o.order_type === "dine_in" ? `Table ${o.table_label}` : o.car_number ? `Car ${o.car_number}` : ORDER_TYPE_LABEL[o.order_type]}
              <span className="muted small"> · {o.kots.map((k) => `${k.prep_area_name}: ${k.status}`).join(" · ")}</span>
            </span>
            <span className={`status-pill st-${o.status}`}>{STATUS_LABEL[o.status]}</span>
            {o.invoice_no ? <span className="badge">Billed</span> : <>
              <button className="link" onClick={() => onAdd(o)}>Add</button>
              {onBill && <button className="link" onClick={() => onBill(o)}>Bill</button>}
              {onBill && o.kots.flatMap((k) => k.lines).length > 0 && <button className="link" onClick={() => setChanging({ order: o, mode: "split" })}>Split</button>}
              {o.order_type === "dine_in" && <button className="link" onClick={() => setChanging({ order: o, mode: "move" })}>Move</button>}
              <button className="link danger" onClick={() => setCancelling(o)}>Cancel</button>
            </>}
            {o.status === "ready" && (
              <button className="link" onClick={() => run(async () => {
                await api(`/orders/${o.id}/complete`, { method: "POST" });
                setOrders((list) => list.filter((x) => x.id !== o.id));
              })}>{o.order_type === "dine_in" ? "Served" : "Handed over"}</button>
            )}
          </li>
        ))}
        {orders.length === 0 && <li className="muted">No open orders.</li>}
      </ul>
      {changing && <SplitMove order={changing.order} mode={changing.mode} tables={tables} onClose={() => setChanging(null)}
        onDone={async () => { setChanging(null); setOrders(await api<Order[]>(`/orders?outlet_id=${outletId}${query}`)); }} />}
      {cancelling && (
        <ApprovalDialog title={`Cancel token #${cancelling.token_no}? The kitchen will stop making it.`} onClose={() => setCancelling(null)}
          onSubmit={async (reason, pin) => {
            await api(`/orders/${cancelling.id}/cancel`, { body: { reason, manager_pin: pin } });
            setOrders((l) => l.filter((x) => x.id !== cancelling.id));
          }} />
      )}
    </div>
  );
}
