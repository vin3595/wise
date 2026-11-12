// Live updates: one stream per outlet shared by every screen on this device. Each note says what
// changed ("orders", "bills", ...); the screen then reloads what it shows. If the stream can't be
// opened (old server, proxy, offline), screens keep checking on their own timer as before.
import { tokenStore } from "./api";

type Listener = (topics: string[]) => void;
interface Feed { listeners: Set<Listener>; live: boolean; stop: () => void }
const feeds = new Map<string, Feed>();
const statusListeners = new Set<() => void>();

function open(url: string, auth: boolean, feed: Feed) {
  let aborted = false;
  let ctrl: AbortController | null = null;
  let wait = 1000;
  const run = async () => {
    while (!aborted) {
      ctrl = new AbortController();
      try {
        const headers: Record<string, string> = { Accept: "text/event-stream" };
        const t = tokenStore.get();
        if (auth && t) headers.Authorization = `Bearer ${t}`;
        const res = await fetch(url, { headers, signal: ctrl.signal, cache: "no-store" });
        if (!res.ok || !res.body) throw new Error(String(res.status));
        setLive(feed, true);
        wait = 1000;
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
            const data = chunk.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
            if (!data) continue;
            try { const m = JSON.parse(data); feed.listeners.forEach((fn) => fn(m.t || [])); } catch { /* ignore */ }
          }
        }
      } catch { /* fall through to retry */ }
      setLive(feed, false);
      if (aborted) return;
      await new Promise((r) => setTimeout(r, wait));
      wait = Math.min(wait * 2, 30000);
    }
  };
  run();
  return () => { aborted = true; ctrl?.abort(); };
}

function setLive(feed: Feed, v: boolean) {
  if (feed.live !== v) { feed.live = v; statusListeners.forEach((fn) => fn()); }
}

/** Subscribe to an outlet's changes. `displayKey` = the public TV screen (no sign-in). */
export function subscribe(target: { outletId?: string; displayKey?: string }, fn: Listener): () => void {
  const url = target.displayKey ? `/api/public/display/${encodeURIComponent(target.displayKey)}/events`
    : `/api/events?outlet_id=${encodeURIComponent(target.outletId || "")}`;
  if (!target.displayKey && !target.outletId) return () => {};
  let feed = feeds.get(url);
  if (!feed) {
    feed = { listeners: new Set(), live: false, stop: () => {} };
    feeds.set(url, feed);
    feed.stop = open(url, !target.displayKey, feed);
  }
  feed.listeners.add(fn);
  const f = feed;
  return () => {
    f.listeners.delete(fn);
    if (f.listeners.size === 0) { f.stop(); feeds.delete(url); }
  };
}

export function isLive(target: { outletId?: string; displayKey?: string }): boolean {
  const url = target.displayKey ? `/api/public/display/${encodeURIComponent(target.displayKey)}/events`
    : `/api/events?outlet_id=${encodeURIComponent(target.outletId || "")}`;
  return !!feeds.get(url)?.live;
}

export function onLiveStatus(fn: () => void): () => void {
  statusListeners.add(fn);
  return () => statusListeners.delete(fn);
}
