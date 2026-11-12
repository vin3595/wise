import { useEffect, useState } from "react";
import { api } from "../api";
import { formatPaise } from "../money";

export interface LoyaltyInfo { mode: "off" | "points" | "stamps" | "both"; phone: string; known: boolean; name: string | null; points_paise: number; stamps: number;
  stamps_needed: number; reward_paise: number; reward_label: string; redeem_min_paise: number; max_redeem_bp: number; visits: number }
export interface LoyaltyUse { points_paise: number; use_reward: boolean }

/** Most points usable on a bill of this size, in whole rupees (same rule as the server). */
export function usablePoints(l: LoyaltyInfo, gross: number): number {
  if (!(l.mode === "points" || l.mode === "both") || l.points_paise < l.redeem_min_paise) return 0;
  return Math.floor(Math.min(l.points_paise, Math.floor((gross * l.max_redeem_bp) / 10000)) / 100) * 100;
}
const rewardReady = (l: LoyaltyInfo) => (l.mode === "stamps" || l.mode === "both") && l.stamps >= l.stamps_needed;

/** Shows what a customer has once their number is typed, with one tap to use points or the reward. */
export function LoyaltyBox({ phone, gross, value, onChange, onInfo }: { phone: string; gross: number; value: LoyaltyUse; onChange: (u: LoyaltyUse) => void; onInfo?: (l: LoyaltyInfo | null) => void }) {
  const [info, setInfo] = useState<LoyaltyInfo | null>(null);
  const digits = phone.replace(/\D/g, "").slice(-10);
  useEffect(() => {
    setInfo(null); onInfo?.(null);
    if (onChange && (value.points_paise || value.use_reward)) onChange({ points_paise: 0, use_reward: false });
    if (!/^[6-9]\d{9}$/.test(digits)) return;
    const t = setTimeout(() => api<LoyaltyInfo>(`/loyalty/customer?phone=${digits}`).then((x) => { setInfo(x); onInfo?.(x); }).catch(() => {}), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digits]);
  if (!info || info.mode === "off") return null;
  const usable = usablePoints(info, gross);
  const showPts = info.mode === "points" || info.mode === "both", showSt = info.mode === "stamps" || info.mode === "both";
  return (
    <div className="loyalty-box">
      <div className="lb-head">{info.known ? <><strong>{info.name || "Returning customer"}</strong> · visit {info.visits + 1}</> : <strong>New customer: this bill starts their {showPts ? "points" : "stamps"}</strong>}</div>
      {info.known && <div className="lb-lines">
        {showPts && <span>Points worth <strong>{formatPaise(info.points_paise)}</strong></span>}
        {showSt && <span className="stamps" aria-label={`${Math.min(info.stamps, info.stamps_needed)} of ${info.stamps_needed} stamps`}>
          {Array.from({ length: info.stamps_needed }, (_, i) => <i key={i} className={i < info.stamps ? "on" : ""} />)}
        </span>}
      </div>}
      <div className="lb-actions">
        {usable > 0 && <button className={`choice ${value.points_paise ? "on" : ""}`} onClick={() => onChange({ ...value, points_paise: value.points_paise ? 0 : usable })}>{value.points_paise ? "✓ " : ""}Use {formatPaise(usable)} of points</button>}
        {info.known && showPts && usable === 0 && info.points_paise > 0 && <span className="muted small">Points can be used from {formatPaise(info.redeem_min_paise)}</span>}
        {rewardReady(info) && <button className={`choice ${value.use_reward ? "on" : ""}`} onClick={() => onChange({ ...value, use_reward: !value.use_reward })}>{value.use_reward ? "✓ " : ""}{info.reward_label}</button>}
      </div>
    </div>
  );
}
