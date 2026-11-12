import { isLive, subscribe } from "./live";
import { useEffect, useRef, useState } from "react";

/** Calls fn now and every `ms` while the tab is visible. Returns the last error, if any. */
/** Runs `fn` now and then every `ms`. With `live` set, it also runs right after each change at that outlet
 *  (debounced), and while the live stream is up the timer only runs every 30 s as a safety net. */
export function usePolling(fn: () => Promise<void>, ms: number, deps: unknown[],
  live?: { outletId?: string; displayKey?: string; topics?: string[] }) {
  const [error, setError] = useState<string | null>(null);
  const saved = useRef(fn);
  saved.current = fn;
  const liveKey = live ? `${live.outletId || ""}|${live.displayKey || ""}|${(live.topics || []).join(",")}` : "";
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    let soon: ReturnType<typeof setTimeout> | undefined;
    let running = false, again = false;
    const tick = async () => {
      clearTimeout(timer);
      if (running) { again = true; return; }
      running = true;
      if (document.visibilityState === "visible") {
        try {
          await saved.current();
          if (!stop) setError(null);
        } catch (e) {
          if (!stop) setError(e instanceof Error ? e.message : "Connection problem");
        }
      }
      running = false;
      if (stop) return;
      if (again) { again = false; tick(); return; }
      const slow = live && isLive(live);
      timer = setTimeout(tick, slow ? Math.max(ms, 30000) : ms);
    };
    const unsub = live ? subscribe(live, (topics) => {
      if (live.topics && !topics.some((t) => live.topics!.includes(t))) return;
      clearTimeout(soon);
      soon = setTimeout(tick, 150);   // several changes in a burst -> one reload
    }) : () => {};
    const vis = () => { if (document.visibilityState === "visible") tick(); };
    document.addEventListener("visibilitychange", vis);
    tick();
    return () => {
      stop = true;
      clearTimeout(timer); clearTimeout(soon);
      unsub();
      document.removeEventListener("visibilitychange", vis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, liveKey]);
  return error;
}

/** Re-renders every second, for live timers. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

let audioCtx: AudioContext | null = null;

/** Short two-tone chime. Browsers only allow sound after the user has tapped the page once. */
export function chime() {
  try {
    audioCtx = audioCtx || new AudioContext();
    const ctx = audioCtx;
    [880, 1320].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.18);
      gain.gain.exponentialRampToValueAtTime(0.3, ctx.currentTime + i * 0.18 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.18 + 0.3);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + i * 0.18);
      osc.stop(ctx.currentTime + i * 0.18 + 0.32);
    });
  } catch {
    /* no audio available */
  }
}

export function unlockAudio() {
  try {
    audioCtx = audioCtx || new AudioContext();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch {
    /* ignore */
  }
}

export function elapsed(fromIso: string, nowMs: number): string {
  const s = Math.max(0, Math.floor((nowMs - new Date(fromIso).getTime()) / 1000));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function ageLevel(fromIso: string, nowMs: number): "ok" | "warn" | "late" {
  const mins = (nowMs - new Date(fromIso).getTime()) / 60000;
  return mins >= 15 ? "late" : mins >= 8 ? "warn" : "ok";
}

export const ORDER_TYPE_LABEL = { dine_in: "Dine-in", takeaway: "Takeaway", delivery: "Delivery" } as const;

/** Opened by a start-up file (?kiosk=1): no one is there to tap, so sound starts on by itself
 *  (the start-up file lets Chrome play sound without a tap). */
export const startedByScreenFile = () => { try { return new URLSearchParams(location.search).has("kiosk"); } catch { return false; } };
