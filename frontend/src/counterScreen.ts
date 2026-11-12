/** The customer-facing second screen on a dual-screen POS.
 *
 * Both screens belong to the same POS computer, so the counter and the customer screen talk straight
 * to each other inside the browser (BroadcastChannel). Nothing goes to the box or the internet:
 * it keeps working when the box is down, and the customer's number only reaches the cashier's cart.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export interface ScreenLine { name: string; qty: number; amount_paise: number }
export interface ScreenState {
  brand: { app_name: string; logo_url: string | null; primary_color: string; outlet: string };
  lines: ScreenLine[];
  total_paise: number;
  discount_paise: number;
  /** Show the touch keypad for mobile number and name. */
  ask: boolean;
  phone: string;
  name: string;
  /** A UPI QR to show big on the customer screen (auto-confirm or the outlet's own QR). */
  qr: { upi: string; amount_paise: number } | null;
  /** Just paid: show thanks for a few seconds. */
  thanks: { amount_paise: number; token_no: number | null; sms: boolean } | null;
  /** Played full screen while the counter is idle. */
  ads: AdItem[];
}
export interface AdItem { id: string; kind: "image" | "video"; url: string; seconds: number }
export type FromScreen = { t: "hello" } | { t: "alive" } | { t: "customer"; phone: string; name: string } | { t: "skip" } | { t: "timeout" };
type FromCounter = { t: "state"; s: ScreenState };

const channelName = () => "wise-counter-screen";

/** Indian mobile: 10 digits starting 6-9 (a leading +91/0 is dropped). */
export function cleanMobile(raw: string): string | null {
  let d = raw.replace(/\D/g, "");
  if (d.length === 12 && d.startsWith("91")) d = d.slice(2);
  if (d.length === 11 && d.startsWith("0")) d = d.slice(1);
  return /^[6-9]\d{9}$/.test(d) ? d : null;
}

/** Counter side: sends the cart to the customer screen; gets back the number the customer typed. */
export function useCounterScreen(state: ScreenState, onMsg: (m: FromScreen) => void) {
  const ch = useRef<BroadcastChannel | null>(null);
  const [connected, setConnected] = useState(false);
  const last = useRef(0);
  const latest = useRef(state);
  latest.current = state;
  const handler = useRef(onMsg);
  handler.current = onMsg;
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const c = new BroadcastChannel(channelName());
    ch.current = c;
    c.onmessage = (e: MessageEvent<FromScreen>) => {
      last.current = Date.now();
      setConnected(true);
      if (e.data.t === "hello") c.postMessage({ t: "state", s: latest.current } satisfies FromCounter);
      else if (e.data.t !== "alive") handler.current(e.data);
    };
    const t = setInterval(() => setConnected(Date.now() - last.current < 8000), 3000);
    return () => { clearInterval(t); c.close(); ch.current = null; };
  }, []);
  const key = JSON.stringify(state);
  useEffect(() => { ch.current?.postMessage({ t: "state", s: latest.current } satisfies FromCounter); }, [key]);
  return connected;
}

/** Customer-screen side. */
export function useScreenSide() {
  const [s, setS] = useState<ScreenState | null>(null);
  const ch = useRef<BroadcastChannel | null>(null);
  useEffect(() => {
    const c = new BroadcastChannel(channelName());
    ch.current = c;
    c.onmessage = (e: MessageEvent<FromCounter>) => { if (e.data.t === "state") setS(e.data.s); };
    c.postMessage({ t: "hello" } satisfies FromScreen);
    const t = setInterval(() => c.postMessage({ t: "alive" } satisfies FromScreen), 3000);
    return () => { clearInterval(t); c.close(); };
  }, []);
  const send = useCallback((m: FromScreen) => ch.current?.postMessage(m), []);
  return { s, send };
}

/** Opens the customer screen on the second monitor when the browser can see it (Chrome asks once). */
export async function openCustomerScreen() {
  const url = "/customer-screen";
  let features = "popup,width=1024,height=768";
  try {
    const w = window as unknown as { getScreenDetails?: () => Promise<{ screens: { isPrimary: boolean; availLeft: number; availTop: number; availWidth: number; availHeight: number }[] }> };
    if (w.getScreenDetails) {
      const d = await w.getScreenDetails();
      const other = d.screens.find((x) => !x.isPrimary);
      if (other) features = `popup,left=${other.availLeft},top=${other.availTop},width=${other.availWidth},height=${other.availHeight}`;
    }
  } catch { /* permission refused: the cashier drags the window across */ }
  window.open(url, "wise-customer-screen", features);
}
