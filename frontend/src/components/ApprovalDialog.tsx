import { useState } from "react";
import { useSession } from "../session";
import { Button, ErrorText, Field, Input, useAction } from "../ui";

/** Reason + manager PIN, for cancelling a bill or an order. Owners/managers don't need a PIN. */
export default function ApprovalDialog({ title, onSubmit, onClose }: {
  title: string; onClose: () => void; onSubmit: (reason: string, pin: string | null) => Promise<void>;
}) {
  const { can } = useSession();
  const isManager = can("approve");
  const [reason, setReason] = useState("");
  const [pin, setPin] = useState("");
  const { busy, error, run } = useAction();
  return (
    <div className="modal-back" onClick={onClose}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={(e) => {
        e.preventDefault();
        run(async () => { await onSubmit(reason, isManager ? null : pin); onClose(); });
      }}>
        <h2>{title}</h2>
        <Field label="Reason"><Input value={reason} onChange={(e) => setReason(e.target.value)} required minLength={3} autoFocus /></Field>
        {!isManager && (
          <Field label="Manager PIN" hint="A manager types their PIN on this device">
            <Input inputMode="numeric" maxLength={4} value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} required />
          </Field>
        )}
        <ErrorText error={error} />
        <div className="row">
          <Button variant="danger" disabled={busy}>Confirm</Button>
          <Button type="button" variant="ghost" onClick={onClose}>Back</Button>
        </div>
      </form>
    </div>
  );
}
