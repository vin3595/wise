import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler, tokenStore } from "./api";
import type { Me, PublicBranding, TokenOut } from "./types";

const LOGIN_ID_KEY = "wise.login_id";
const OUTLET_KEY = "wise.device_outlet";

interface Session {
  loading: boolean;
  loginId: string | null;
  branding: PublicBranding | null;
  me: Me | null;
  deviceOutletId: string | null;
  can: (perm: string) => boolean;
  chooseRestaurant: (loginId: string) => Promise<void>;
  forgetRestaurant: () => void;
  setDeviceOutlet: (id: string | null) => void;
  signedIn: (t: TokenOut) => Promise<void>;
  signOut: () => void;
  refresh: () => Promise<void>;
}

const Ctx = createContext<Session | null>(null);

/** Applies the restaurant's colour, title, icon and PWA manifest so the app looks like theirs. */
function applyBranding(b: PublicBranding | null) {
  const root = document.documentElement;
  root.style.setProperty("--brand", b?.primary_color || "#2563eb");
  root.dataset.theme = b?.ui_theme || "steel";
  document.title = b?.app_name || "Wise";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", b?.primary_color || "#2563eb");
  const manifest = document.getElementById("app-manifest");
  manifest?.setAttribute("href", b ? `/api/public/tenants/${b.login_id}/manifest.webmanifest` : "/manifest.webmanifest");
  const icon = b?.logo_url || "/icon.svg";
  document.getElementById("app-touch-icon")?.setAttribute("href", b?.logo_url || "/apple-touch-icon.png");
  document.querySelector('link[rel="icon"]')?.setAttribute("href", icon);
}

async function detectLoginId(): Promise<string | null> {
  const fromQuery = new URLSearchParams(location.search).get("r");
  if (fromQuery) return fromQuery.toLowerCase();
  const saved = localStorage.getItem(LOGIN_ID_KEY);
  if (saved) return saved;
  try {
    const res = await api<{ login_id: string | null }>(`/public/resolve-host?host=${encodeURIComponent(location.host)}`);
    return res.login_id;
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [loginId, setLoginId] = useState<string | null>(null);
  const [branding, setBranding] = useState<PublicBranding | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [deviceOutletId, setDeviceOutletState] = useState<string | null>(localStorage.getItem(OUTLET_KEY));

  const loadBranding = useCallback(async (id: string) => {
    const b = await api<PublicBranding>(`/public/tenants/${encodeURIComponent(id)}`);
    localStorage.setItem(LOGIN_ID_KEY, b.login_id);
    setLoginId(b.login_id);
    setBranding(b);
    applyBranding(b);
  }, []);

  const refresh = useCallback(async () => {
    if (!tokenStore.get()) {
      setMe(null);
      return;
    }
    try {
      setMe(await api<Me>("/tenant/me"));
    } catch {
      setMe(null);
    }
  }, []);

  const signOut = useCallback(() => {
    tokenStore.clear();
    setMe(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(signOut);
    (async () => {
      const id = await detectLoginId();
      if (id) {
        try {
          await loadBranding(id);
        } catch {
          localStorage.removeItem(LOGIN_ID_KEY);
        }
      }
      await refresh();
      setLoading(false);
    })();
  }, [loadBranding, refresh, signOut]);

  const value = useMemo<Session>(
    () => ({
      loading,
      loginId,
      branding,
      me,
      deviceOutletId,
      can: (perm) => !!me?.permissions.includes(perm),
      chooseRestaurant: loadBranding,
      forgetRestaurant: () => {
        localStorage.removeItem(LOGIN_ID_KEY);
        localStorage.removeItem(OUTLET_KEY);
        tokenStore.clear();
        setLoginId(null);
        setBranding(null);
        setMe(null);
        setDeviceOutletState(null);
        applyBranding(null);
      },
      setDeviceOutlet: (id) => {
        if (id) localStorage.setItem(OUTLET_KEY, id);
        else localStorage.removeItem(OUTLET_KEY);
        setDeviceOutletState(id);
      },
      signedIn: async (t) => {
        tokenStore.set(t.token);
        if (t.tenant.login_id !== loginId) await loadBranding(t.tenant.login_id);
        await refresh();
      },
      signOut,
      refresh,
    }),
    [loading, loginId, branding, me, deviceOutletId, loadBranding, refresh, signOut]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}
