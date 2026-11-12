// Billing on the counter while the box can't be reached.
//
// While online the counter registers once and keeps an "offline pack" (its own invoice series, a block of
// token numbers, GST rate per item, the bill header) plus the outlet's menu in this browser's storage.
// When the box stops answering, the counter keeps billing from that copy: same GST maths as the box
// (gst.ts), its own numbers, bill and KOT slips printed here. Each bill waits in a queue on this device
// and is sent to the box as soon as it answers again. Taking a number and queueing the bill happen in one
// storage write, so a number is never used without its bill being kept.

import { api, ApiError, newId } from "./api";
import { computeBill, fyStart, invoiceNumber, type TaxRate } from "./gst";
import type { Bill, Outlet, OutletMenu, OutletMenuItem, OutletMenuVariant, OrderType } from "./types";

export interface OfflinePack {
  device_id: string;
  name: string;
  letter: string;
  series: string;
  token_base: number;
  fy_start: number;
  fy_start_month: number;
  last_seq: number;
  items: Record<string, TaxRate & { hsn_sac: string | null }>;
  prep_areas: Record<string, string>;
  outlet: Bill["outlet"];
}

export interface QueuedLine {
  variant_id: string;
  qty: number;
  price_paise: number;
  addons: { id: string; name: string; price_paise: number }[];
  notes: string | null;
  tax: TaxRate;
}

export interface QueuedBill {
  id: string;
  device_id: string;
  made_at: string;
  made_by: string;
  fy_start: number;
  seq: number;
  invoice_no: string;
  token_no: number;
  order_type: OrderType;
  table_label: string | null;
  car_number: string | null;
  customer_name: string | null;
  notes: string | null;
  customer: { name: string | null; phone: string | null; declined: boolean };
  lines: QueuedLine[];
  payments: { mode: "cash" | "upi" | "card"; amount_paise: number }[];
  total_paise: number;
  tried_order_id: string | null;
  error?: string;          // set when the box refused it; stays queued for the manager
}

interface Store {
  device_id?: string;
  outlet_id?: string;
  devices?: Record<string, string>;     // outlet id -> this browser's counter id there
  pack?: OfflinePack;
  last_seq: Record<string, number>;     // "SERIES/FY" -> last number used on this device
  last_token: Record<string, number>;   // business date -> last offline token
  queue: QueuedBill[];
  menus: Record<string, OutletMenu>;
  outlets?: Outlet[];
}

const KEY = "wise.offline";
function load(): Store {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || "{}");
    return { last_seq: {}, last_token: {}, queue: [], menus: {}, ...s };
  } catch {
    return { last_seq: {}, last_token: {}, queue: [], menus: {} };
  }
}
function save(s: Store) {
  localStorage.setItem(KEY, JSON.stringify(s));
  emit();
}

// ------------------------------------------------------------------ connection state

let online = true;
let syncing = false;
let lastNotes: string[] = [];
const listeners = new Set<() => void>();
function emit() { listeners.forEach((f) => f()); }
export function subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; }

export interface OfflineState { online: boolean; syncing: boolean; queued: number; failed: QueuedBill[]; notes: string[]; ready: boolean; series: string | null }
export function state(): OfflineState {
  const s = load();
  return { online, syncing, queued: s.queue.length, failed: s.queue.filter((b) => b.error), notes: lastNotes,
    ready: !!s.pack, series: s.pack?.series ?? null };
}

/** True for "the box didn't answer" (no network, or the box's web server up but the API down). */
export function isOffline(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 0 || e.status === 502 || e.status === 503 || e.status === 504);
}

let pinger: ReturnType<typeof setInterval> | null = null;
export function goOffline() {
  if (!online) return;
  online = false;
  emit();
  if (!pinger) pinger = setInterval(checkBox, 4000);
}
async function checkBox() {
  try {
    await api("/health");
  } catch {
    return;
  }
  online = true;
  if (pinger) { clearInterval(pinger); pinger = null; }
  emit();
  void syncNow();
}

// ------------------------------------------------------------------ setup while online

let preparing: Promise<OfflinePack | null> | null = null;
export function prepare(outletId: string): Promise<OfflinePack | null> {
  // One registration at a time, and the device id is stored before asking, so a screen that loads twice
  // never registers this counter as two counters.
  if (!preparing) preparing = doPrepare(outletId).finally(() => { preparing = null; });
  return preparing;
}
async function doPrepare(outletId: string): Promise<OfflinePack | null> {
  let s = load();
  if (s.outlet_id !== outletId || !s.device_id) {
    const id = s.devices?.[outletId] ?? newId();
    s = { ...s, device_id: id, outlet_id: outletId, pack: undefined, devices: { ...s.devices, [outletId]: id } };
    save(s);
  }
  try {
    const pack = await api<OfflinePack>("/offline/devices", { body: { id: s.device_id, outlet_id: outletId, name: "Counter" } });
    save({ ...load(), pack });
    return pack;
  } catch (e) {
    if (isOffline(e)) goOffline();
    return s.pack ?? null;
  }
}

export function rememberMenu(outletId: string, menu: OutletMenu) { const s = load(); s.menus[outletId] = menu; save(s); }
export function savedMenu(outletId: string): OutletMenu | null { return load().menus[outletId] ?? null; }
export function rememberOutlets(o: Outlet[]) { const s = load(); s.outlets = o; save(s); }
export function savedOutlets(): Outlet[] { return load().outlets ?? []; }

// ------------------------------------------------------------------ billing offline

export interface CartIn { item: OutletMenuItem; variant: OutletMenuVariant; addonIds: string[]; qty: number }

function taxFor(pack: OfflinePack, itemId: string): TaxRate {
  const t = pack.items[itemId];
  return t ? { cgst_bp: t.cgst_bp, sgst_bp: t.sgst_bp, igst_bp: t.igst_bp, inclusive: t.inclusive } : { cgst_bp: 0, sgst_bp: 0, igst_bp: 0, inclusive: false };
}
function addonsOf(l: CartIn) {
  return l.item.addon_groups.flatMap((g) => g.addons).filter((a) => a.id && l.addonIds.includes(a.id))
    .map((a) => ({ id: a.id as string, name: a.name, price_paise: a.price_paise }));
}

/** The bill total for a cart, using the saved GST rates (no box needed). */
export function quoteOffline(cart: CartIn[]): number | null {
  const pack = load().pack;
  if (!pack || !cart.length) return null;
  return computeBill(cart.map((l) => ({ qty: l.qty, unit_paise: l.variant.price_paise + addonsOf(l).reduce((s, a) => s + a.price_paise, 0), tax: taxFor(pack, l.item.item_id) }))).total_paise;
}

/** Business day as the box counts it: India time, a new day from 4 am. */
export function businessDate(d = new Date()): string {
  const ist = new Date(d.getTime() + (330 - 240) * 60000);
  return ist.toISOString().slice(0, 10);
}

export interface OfflineResult { bill: Bill; kots: { area: string; lines: { qty: number; name: string; extra: string }[] }[]; queued: QueuedBill }

export function billOffline(p: {
  cart: CartIn[]; madeBy: { id: string; name: string }; orderType: OrderType; table: string | null; car: string | null;
  customerName: string | null; notes: string | null; phone: string | null; declined: boolean; mode: "cash" | "upi" | "card";
  triedOrderId: string | null;
}): OfflineResult {
  const s = load();
  const pack = s.pack;
  if (!pack || !s.device_id) throw new Error("This counter wasn't set up for offline billing before the box went down. Bill on paper and enter it later.");

  const now = new Date();
  const day = businessDate(now);
  const fy = fyStart(new Date(day + "T12:00:00"), pack.fy_start_month);
  const k = `${pack.series}/${fy}`;
  const seq = Math.max(s.last_seq[k] ?? 0, fy === pack.fy_start ? pack.last_seq : 0) + 1;
  const token = Math.max(s.last_token[day] ?? 0, pack.token_base) + 1;
  const invoice_no = invoiceNumber(pack.series, fy, seq);

  const lines: QueuedLine[] = p.cart.map((l) => ({ variant_id: l.variant.variant_id, qty: l.qty, price_paise: l.variant.price_paise,
    addons: addonsOf(l), notes: null, tax: taxFor(pack, l.item.item_id) }));
  const calc = computeBill(lines.map((l) => ({ qty: l.qty, unit_paise: l.price_paise + l.addons.reduce((a, b) => a + b.price_paise, 0), tax: l.tax })));
  const q: QueuedBill = {
    id: newId(), device_id: s.device_id, made_at: now.toISOString(), made_by: p.madeBy.id, fy_start: fy, seq, invoice_no,
    token_no: token, order_type: p.orderType, table_label: p.table, car_number: p.car, customer_name: p.customerName, notes: p.notes,
    customer: { name: p.customerName, phone: p.declined ? null : p.phone, declined: p.declined }, lines,
    payments: [{ mode: p.mode, amount_paise: calc.total_paise }], total_paise: calc.total_paise, tried_order_id: p.triedOrderId,
  };
  // One write: the number, the token and the bill are kept together or not at all.
  save({ ...s, last_seq: { ...s.last_seq, [k]: seq }, last_token: { ...s.last_token, [day]: token }, queue: [...s.queue, q] });

  const summary = new Map<number, Bill["tax_summary"][number]>();
  const billLines = p.cart.map((l, i) => {
    const c = calc.lines[i];
    const row = summary.get(c.rate_bp) ?? { rate_bp: c.rate_bp, taxable_paise: 0, cgst_paise: 0, sgst_paise: 0, igst_paise: 0 };
    row.taxable_paise += c.taxable_paise; row.cgst_paise += c.cgst_paise; row.sgst_paise += c.sgst_paise; row.igst_paise += c.igst_paise;
    summary.set(c.rate_bp, row);
    return { item_name: l.item.name, variant_name: l.variant.name, addons_text: lines[i].addons.map((a) => a.name).join(", ") || null,
      hsn_sac: pack.items[l.item.item_id]?.hsn_sac ?? null, qty: l.qty, unit_paise: lines[i].price_paise + lines[i].addons.reduce((a, b) => a + b.price_paise, 0),
      gross_paise: c.gross_paise, discount_paise: 0, taxable_paise: c.taxable_paise, cgst_paise: c.cgst_paise, sgst_paise: c.sgst_paise,
      igst_paise: c.igst_paise, rate_bp: c.rate_bp, tax_inclusive: c.inclusive };
  });
  const bill: Bill = {
    id: q.id, invoice_no, status: "paid", order_id: "", token_no: token, order_type: p.orderType, table_label: p.table, car_number: p.car,
    business_date: day, created_at: q.made_at, customer_name: p.customerName, customer_phone: q.customer.phone, customer_gstin: null,
    discount_reason: null, payments: [{ mode: p.mode, amount_paise: calc.total_paise, reference: null }], cashier_name: p.madeBy.name,
    cancel_reason: null, cancelled_at: null, outlet: pack.outlet, upi_link: null, lines: billLines, gross_paise: calc.gross_paise,
    discount_paise: 0, taxable_paise: calc.taxable_paise, cgst_paise: calc.cgst_paise, sgst_paise: calc.sgst_paise, igst_paise: calc.igst_paise,
    round_off_paise: calc.round_off_paise, total_paise: calc.total_paise, tax_summary: [...summary.values()].sort((a, b) => a.rate_bp - b.rate_bp),
    needs_approval: [],
  };
  const byArea = new Map<string, OfflineResult["kots"][number]>();
  p.cart.forEach((l, i) => {
    const area = (l.item.prep_area_id && pack.prep_areas[l.item.prep_area_id]) || "Kitchen";
    const k2 = byArea.get(area) ?? { area, lines: [] };
    k2.lines.push({ qty: l.qty, name: `${l.item.name}${l.variant.name && l.variant.name !== "Regular" ? ` (${l.variant.name})` : ""}`, extra: billLines[i].addons_text || "" });
    byArea.set(area, k2);
  });
  return { bill, kots: [...byArea.values()], queued: q };
}

// ------------------------------------------------------------------ sending to the box

export async function syncNow(): Promise<void> {
  if (syncing) return;
  syncing = true;
  emit();
  const notes: string[] = [];
  try {
    for (const q of load().queue) {
      try {
        const body: Partial<QueuedBill> = { ...q };
        delete body.error;
        const r = await api<{ notes: string[] }>("/offline/bills", { body });
        notes.push(...r.notes.map((n) => `${q.invoice_no}: ${n}`));
        const s = load();
        save({ ...s, queue: s.queue.filter((x) => x.id !== q.id) });
      } catch (e) {
        if (isOffline(e)) { goOffline(); break; }
        if (e instanceof ApiError && e.status === 401) break;   // signed out: try again after sign-in
        const s = load();
        save({ ...s, queue: s.queue.map((x) => (x.id === q.id ? { ...x, error: (e as Error).message } : x)) });
      }
    }
  } finally {
    syncing = false;
    if (notes.length) lastNotes = notes;
    emit();
  }
}

export function queuedBills(): QueuedBill[] { return load().queue; }
