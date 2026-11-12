import { ORDER_TYPE_LABEL } from "../hooks";
import type { PrintJob } from "../types";

/** The printed KOT, same layout as the print bridge's ESC/POS slip: area, big token, table/car, items. */
export function KotSlip({ job, copy = 1, copies = 1, paper = 80 }: { job: PrintJob; copy?: number; copies?: number; paper?: number }) {
  const k = job.kot;
  const where = k.table_label ? `Table ${k.table_label}` : k.car_number ? `Car ${k.car_number}` : null;
  const count = k.lines.reduce((n, l) => n + l.qty, 0);
  const at = new Date(k.created_at).toLocaleString("en-IN", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "numeric", minute: "2-digit" });
  return (
    <div className={`kot-slip paper-${paper} ${job.kind}`}>
      <div className="ks-area">{k.prep_area_name}</div>
      {job.kind === "cancel" && <div className="ks-flag">Cancelled<small>Do not make this order</small></div>}
      {job.kind === "reprint" && <div className="ks-mark">Reprint {job.printed_before + 1}</div>}
      {k.is_addon_kot && job.kind !== "cancel" && <div className="ks-mark">Added items</div>}
      {copies > 1 && <div className="ks-small">Copy {copy} of {copies}</div>}
      <div className="ks-token">Token {k.token_no}</div>
      {where && <div className="ks-where">{where}</div>}
      <div className="ks-type">{ORDER_TYPE_LABEL[k.order_type]}</div>
      <div className="ks-small">KOT {k.kot_no} · {at}</div>
      {(k.customer_name || job.taken_by) && (
        <div className="ks-small">{[k.customer_name && `For ${k.customer_name}`, job.taken_by && `By ${job.taken_by}`].filter(Boolean).join("  ")}</div>
      )}
      <hr />
      {k.lines.map((l) => (
        <div key={l.id} className="ks-line">
          <strong>{l.qty} x {l.item_name}{l.variant_name !== "Regular" ? ` (${l.variant_name})` : ""}</strong>
          {l.addons.length > 0 && <div>+ {l.addons.map((a) => a.name).join(", ")}</div>}
          {l.notes && <div><b>Note: {l.notes}</b></div>}
        </div>
      ))}
      <hr />
      {k.order_notes && <div className="ks-line"><b>Order note: {k.order_notes}</b></div>}
      <div>Items: {count}</div>
    </div>
  );
}
