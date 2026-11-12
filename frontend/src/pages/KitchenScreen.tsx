import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useSession } from "../session";
import type { TokenOut } from "../types";
import Kds from "./Kds";

/** The kitchen display opened from its link (/kitchen/<key>): signs itself in, no PIN, no daily login. */
export default function KitchenScreen({ kitchenKey }: { kitchenKey: string }) {
  const { loading, me, signedIn } = useSession();
  const [error, setError] = useState<string | null>(null);
  const trying = useRef(false);
  const signIn = async () => {
    if (trying.current) return;
    trying.current = true;
    try {
      await signedIn(await api<TokenOut>("/auth/kitchen-screen", { body: { key: kitchenKey } }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Can't reach the box");
      setTimeout(() => { trying.current = false; signIn(); }, 15000);   // box still starting: keep trying
      return;
    }
    trying.current = false;
  };
  // Sign in at start, whenever the session drops, and every 6 hours so it never runs out mid-service.
  useEffect(() => {
    if (loading) return;
    if (!me || !me.user.is_screen) signIn();
    const t = setInterval(signIn, 6 * 3600 * 1000);
    return () => clearInterval(t);
  }, [loading, me?.user.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!me || !me.user.is_screen) return <div className="splash">{error || "Starting the kitchen display…"}</div>;
  return <div className="kitchen-standalone"><Kds /></div>;
}
