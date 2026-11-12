import { useEffect, useMemo, useState } from "react";
import { api, newId } from "../api";
import { formatPaise, inputToPaise, paiseToInput } from "../money";
import { QrImage } from "../qr";
import type { Bill, BillPreview, Order, Outlet, PayMode } from "../types";
import { Button, ErrorText, Field, Input, useAction } from "../ui";
import { LoyaltyBox, type LoyaltyUse } from "./Loyalty";
import type { UpiReq } from "./UpiAuto";

const MODES: { mode: PayMode; label: string }[] = [
  { mode: "cash", label: "Cash" }, { mode: "upi", label: "UPI" }, { mode: "card", label: "Card" },
  { mode: "due", label: "Credit" }, { mode: "complimentary", label: "Complimentary" },
];

interface PayRow { mode: PayMode; amount: string; reference: string }

/** Settle an order: discount, customer, payments (split allowed), manager PIN when needed, then print. */
export default function BillDialog({ order, outlet, onClose, onBilled, upiAuto, onUpiRequest }: {
  order: Order; outlet: Outlet | undefined; onClose: () => void; onBilled: (b: Bill) => void;
  upiAuto?: boolean; onUpiRequest?: (r: UpiReq) => void;
}) {
  const [preview, setPreview] = useState<BillPreview | null>(null);
  const [discKind, setDiscKind] = useState<"percent" | "flat">("percent");
  const [discValue, setDiscValue] = useState("");
  const [discReason, setDiscReason] = useState("");
  const [phone, setPhone] = useState(order.customer_phone || "");
  const [name, setName] = useState(order.customer_name || "");
  const [declined, setDeclined] = useState(false);
  const [gstin, setGstin] = useState("");
  const [showGst, setShowGst] = useState(false);
  const [pays, setPays] = useState<PayRow[]>([{ mode: "cash", amount: "", reference: "" }]);
  const [equal, setEqual] = useState<PayMode[] | null>(null);   // "split equally": how each person pays
  const [tendered, setTendered] = useState("");
  const [pin, setPin] = useState("");
  const [billId] = useState(newId);
  const [loy, setLoy] = useState<LoyaltyUse>({ points_paise: 0, use_reward: false });
  const usingLoyalty = loy.points_paise > 0 || loy.use_reward;
  const pv = useAction();
  const save = useAction();

  const discount = useMemo(() => {
    const n = discKind === "percent" ? Math.round(Number(discValue || "0") * 100) : inputToPaise(discValue || "0") ?? 0;
    return n > 0 ? { kind: discKind, value: n, reason: discReason || null } : null;
  }, [discKind, discValue, discReason]);

  useEffect(() => {
    const t = setTimeout(() => pv.run(async () => {
      setPreview(await api<BillPreview>("/bills/preview", { body: { order_id: order.id, discount: usingLoyalty ? null : discount,
        loyalty: usingLoyalty ? { ...loy, phone } : null } }));
    }), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order.id, discount?.kind, discount?.value, loy.points_paise, loy.use_reward]);

  const total = preview?.total_paise ?? 0;
  // A single payment row always takes the whole amount.
  const rows: PayRow[] = pays.length === 1 ? [{ ...pays[0], amount: paiseToInput(total) }] : pays;
  const paid = rows.reduce((s, r) => s + (inputToPaise(r.amount) ?? 0), 0);
  const cashRow = rows.find((r) => r.mode === "cash");
  const change = cashRow && tendered ? (inputToPaise(tendered) ?? 0) - (inputToPaise(cashRow.amount) ?? 0) : null;
  const needsPin = (preview?.needs_approval.length ?? 0) > 0 || rows.some((r) => r.mode === "complimentary");
  const upiRow = rows.find((r) => r.mode === "upi");
  const upiAmount = upiRow ? inputToPaise(upiRow.amount) ?? 0 : 0;
  const upiLink = outlet?.upi_vpa && upiAmount > 0
    ? `upi://pay?pa=${encodeURIComponent(outlet.upi_vpa)}&pn=${encodeURIComponent(outlet.upi_payee_name || outlet.name)}&am=${(upiAmount / 100).toFixed(2)}&cu=INR&tn=${encodeURIComponent(`Token ${order.token_no}`)}`
    : null;

  function settle() {
    save.run(async () => {
      const bill = await api<Bill>("/bills", { body: {
        id: billId, order_id: order.id, discount: usingLoyalty ? null : discount, loyalty: usingLoyalty ? loy : null,
        customer: { name: name || null, phone: declined ? null : phone || null, declined, gstin: showGst ? gstin || null : null },
        payments: rows.map((r) => ({ mode: r.mode, amount_paise: inputToPaise(r.amount) ?? 0, reference: r.reference || null })),
        manager_pin: needsPin ? pin || null : null,
      } });
      onBilled(bill);
    });
  }

  /** UPI that confirms itself: this bill's own QR (on screen or printed); the bill is made when the money lands. */
  const upiQr = () => save.run(async () => {
    if (!phone && !declined) throw new Error("Ask for the customer's mobile number, or tap No number");
    const r = await api<UpiReq>("/upi/requests", { body: { order_id: order.id, bill_id: billId,
      loyalty: usingLoyalty ? { ...loy, phone } : null, customer: { name: name || null, phone: declined ? null : phone, declined } } });
    onUpiRequest?.(r);
  });

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="page-head">
          <h2>Bill · Token #{order.token_no}{order.table_label ? ` · Table ${order.table_label}` : order.car_number ? ` · Car ${order.car_number}` : ""}</h2>
          <button className="btn btn-ghost dialog-close" onClick={onClose}>Close</button>
        </div>
        <div className="bill-grid">
          <div>
            <table className="table">
              <tbody>
                {preview?.lines.map((l, i) => (
                  <tr key={i}><td>{l.qty}× {l.item_name}{l.variant_name !== "Regular" ? ` (${l.variant_name})` : ""}{l.addons_text && <div className="muted small">+ {l.addons_text}</div>}</td>
                    <td className="num">{formatPaise(l.gross_paise)}</td></tr>
                ))}
              </tbody>
            </table>
            {preview && (
              <div className="totals">
                <div><span>Subtotal</span><span>{formatPaise(preview.gross_paise)}</span></div>
                {preview.discount_paise > 0 && <div><span>Discount</span><span>−{formatPaise(preview.discount_paise)}</span></div>}
                <div><span>CGST + SGST</span><span>{formatPaise(preview.cgst_paise + preview.sgst_paise + preview.igst_paise)}</span></div>
                {preview.round_off_paise !== 0 && <div><span>Round off</span><span>{(preview.round_off_paise / 100).toFixed(2)}</span></div>}
                <div className="grand"><span>Total</span><span>{formatPaise(preview.total_paise)}</span></div>
              </div>
            )}
            <ErrorText error={pv.error} />
            <h3>Discount</h3>
            <div className="row">
              <div className="tabs mini">
                <button className={discKind === "percent" ? "active" : ""} onClick={() => setDiscKind("percent")}>%</button>
                <button className={discKind === "flat" ? "active" : ""} onClick={() => setDiscKind("flat")}>₹</button>
              </div>
              <Input inputMode="decimal" placeholder={usingLoyalty ? "Loyalty in use" : discKind === "percent" ? "e.g. 10" : "e.g. 50"} disabled={usingLoyalty} value={discValue} onChange={(e) => setDiscValue(e.target.value)} />
              {discount && <Input placeholder="Reason (required)" value={discReason} onChange={(e) => setDiscReason(e.target.value)} />}
            </div>
          </div>
          <div>
            <h3>Customer</h3>
            {!declined && <Input inputMode="tel" placeholder="Mobile number" value={phone} onChange={(e) => setPhone(e.target.value)} />}
            <div className="row">
              <label className="toggle"><input type="checkbox" checked={declined} onChange={(e) => setDeclined(e.target.checked)} /> Customer won't share number</label>
              <button className="link" onClick={() => setShowGst(!showGst)}>{showGst ? "No GST bill" : "Business GST bill"}</button>
            </div>
            <Input placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
            {showGst && <Input placeholder="Customer GSTIN" value={gstin} onChange={(e) => setGstin(e.target.value.toUpperCase())} />}
            {!declined && <LoyaltyBox phone={phone} gross={preview?.gross_paise ?? 0} value={loy} onChange={(u) => { setLoy(u); if (u.points_paise || u.use_reward) setDiscValue(""); }} />}

            <h3>Payment</h3>
            {pays.map((p, i) => (
              <div key={i} className="pay-row">
                <div className="chips">
                  {MODES.map((m) => (
                    <button key={m.mode} className={`choice ${p.mode === m.mode ? "on" : ""}`}
                      onClick={() => setPays(pays.map((x, j) => (j === i ? { ...x, mode: m.mode } : x)))}>{m.label}</button>
                  ))}
                </div>
                {pays.length > 1 && (
                  <div className="row">
                    <Input inputMode="decimal" placeholder="Amount ₹" value={p.amount} onChange={(e) => setPays(pays.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} />
                    <button className="link" onClick={() => setPays(pays.filter((_, j) => j !== i))}>Remove</button>
                  </div>
                )}
                {(p.mode === "upi" || p.mode === "card") && (
                  <Input placeholder={p.mode === "upi" ? "UPI ref (optional)" : "Card last 4 (optional)"} value={p.reference}
                    onChange={(e) => setPays(pays.map((x, j) => (j === i ? { ...x, reference: e.target.value } : x)))} />
                )}
              </div>
            ))}
            <div className="row wrap">
              {pays.length < 3 && <button className="link" onClick={() => setPays([...pays.map((p) => (pays.length === 1 ? { ...p, amount: paiseToInput(total) } : p)), { mode: "upi", amount: "", reference: "" }])}>+ Split payment</button>}
              {preview && total > 0 && <button className="link" onClick={() => setEqual(equal ? null : ["cash", "upi"])}>{equal ? "Close equal split" : "Split equally"}</button>}
            </div>
            {equal && preview && (
              <div className="equal-split">
                <div className="row">
                  <span className="grow">Between <strong>{equal.length}</strong> people: <strong>{formatPaise(Math.floor(total / equal.length))}</strong> each{total % equal.length ? <span className="muted small"> (+1 paisa for some)</span> : null}</span>
                  <button className="choice" disabled={equal.length <= 2} onClick={() => setEqual(equal.slice(0, -1))} aria-label="One person fewer">−</button>
                  <button className="choice" disabled={equal.length >= 20} onClick={() => setEqual([...equal, "upi"])} aria-label="One more person">+</button>
                </div>
                <ol className="equal-people">
                  {equal.map((m, i) => (
                    <li key={i}><span>Person {i + 1}</span>
                      {MODES.filter((x) => x.mode !== "complimentary" && x.mode !== "due").map((x) => (
                        <button key={x.mode} className={`choice ${m === x.mode ? "on" : ""}`} onClick={() => setEqual(equal.map((y, j) => (j === i ? x.mode : y)))}>{x.label}</button>
                      ))}
                    </li>
                  ))}
                </ol>
                <Button variant="ghost" onClick={() => {
                  // Same-mode shares add up into one payment line (2 people on UPI = one UPI line for both).
                  const base = Math.floor(total / equal.length), extra = total - base * equal.length;
                  const by: Record<string, number> = {};
                  equal.forEach((m, i) => { by[m] = (by[m] || 0) + base + (i < extra ? 1 : 0); });
                  setPays(Object.entries(by).map(([mode, amt]) => ({ mode: mode as PayMode, amount: paiseToInput(amt), reference: "" })));
                  setEqual(null);
                }}>Use this split</Button>
              </div>
            )}
            {pays.length > 1 && <p className={paid === total ? "ok" : "error"}>Entered {formatPaise(paid)} of {formatPaise(total)}</p>}
            {cashRow && (
              <Field label="Cash received (for change)">
                <Input inputMode="decimal" value={tendered} onChange={(e) => setTendered(e.target.value)} placeholder="₹" />
              </Field>
            )}
            {change !== null && change >= 0 && <p className="change">Return change: <strong>{formatPaise(change)}</strong></p>}
            {upiLink && !(upiAuto && onUpiRequest) && <div className="upi-box"><QrImage text={upiLink} size={180} label="UPI QR" /><span>Customer scans to pay {formatPaise(upiAmount)}</span></div>}
            {needsPin && (
              <Field label={`Manager PIN (${[...(preview?.needs_approval || []), ...(rows.some((r) => r.mode === "complimentary") ? ["Complimentary"] : [])].join(", ")})`}>
                <Input inputMode="numeric" maxLength={4} value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} placeholder="Ask a manager" />
              </Field>
            )}
            <ErrorText error={save.error} />
            {upiAuto && onUpiRequest && rows.length === 1 && rows[0].mode === "upi" && !discount ? (
              <Button className="btn-block" disabled={save.busy || !preview} onClick={upiQr}>
                UPI QR for {preview ? formatPaise(total) : ""} (bill prints when paid)
              </Button>
            ) : (
              <Button className="btn-block" disabled={save.busy || !preview || (pays.length > 1 && paid !== total)} onClick={settle}>
                Settle & print {preview ? formatPaise(total) : ""}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
