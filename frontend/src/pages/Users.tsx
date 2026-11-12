import { useEffect, useState } from "react";
import { api } from "../api";
import { useSession } from "../session";
import type { Outlet, Role, User } from "../types";
import { Button, Card, ErrorText, Field, Input, Select, Toggle, useAction } from "../ui";

const ROLE_LABEL: Record<Role, string> = {
  owner: "Owner", manager: "Manager", cashier: "Cashier", captain: "Captain", kitchen: "Kitchen",
};
const OTP_ROLES: Role[] = ["owner", "manager"];

interface Form { name: string; role: Role; email: string; phone: string; outlet_ids: string[]; pin: string; is_active: boolean }

export default function Users() {
  const { me } = useSession();
  const [users, setUsers] = useState<User[]>([]);
  const [outlets, setOutlets] = useState<Outlet[]>([]);
  const [editing, setEditing] = useState<{ id: string | null; form: Form } | null>(null);
  const [pinFor, setPinFor] = useState<User | null>(null);
  const { busy, error, run } = useAction();
  const roles: Role[] = me?.user.role === "owner" ? ["owner", "manager", "cashier", "captain", "kitchen"] : ["cashier", "captain", "kitchen"];

  const load = async () => {
    const [u, o] = await Promise.all([api<User[]>("/users"), api<Outlet[]>("/tenant/outlets")]);
    setUsers(u);
    setOutlets(o);
  };
  useEffect(() => { load(); }, []);

  const f = editing?.form;
  const set = <K extends keyof Form>(k: K, v: Form[K]) => editing && setEditing({ ...editing, form: { ...editing.form, [k]: v } });
  const outletNames = (ids: string[]) => ids.map((id) => outlets.find((o) => o.id === id)?.name).filter(Boolean).join(", ");

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Staff</h1>
        {!editing && <Button onClick={() => setEditing({ id: null, form: { name: "", role: "cashier", email: "", phone: "", outlet_ids: outlets.slice(0, 1).map((o) => o.id), pin: "", is_active: true } })}>Add person</Button>}
      </div>
      <p className="muted">Owners and managers sign in with their email or mobile. Cashiers, captains and kitchen staff use a 4-digit PIN on the outlet's device.</p>

      {editing && f && (
        <Card title={editing.id ? "Edit person" : "New person"}>
          <form onSubmit={(e) => {
            e.preventDefault();
            run(async () => {
              const body = { name: f.name, role: f.role, email: f.email || null, phone: f.phone || null,
                outlet_ids: f.role === "owner" ? [] : f.outlet_ids, is_active: f.is_active, ...(editing.id ? {} : { pin: f.pin || null }) };
              await api(editing.id ? `/users/${editing.id}` : "/users", { method: editing.id ? "PATCH" : "POST", body });
              setEditing(null);
              await load();
            });
          }}>
            <div className="grid2">
              <Field label="Name"><Input value={f.name} onChange={(e) => set("name", e.target.value)} required /></Field>
              <Field label="Role"><Select value={f.role} onChange={(e) => set("role", e.target.value as Role)}>
                {roles.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
              </Select></Field>
              <Field label="Mobile"><Input inputMode="tel" value={f.phone} onChange={(e) => set("phone", e.target.value)} /></Field>
              <Field label="Email"><Input type="email" value={f.email} onChange={(e) => set("email", e.target.value)} /></Field>
              {!editing.id && !OTP_ROLES.includes(f.role) && (
                <Field label="4-digit PIN" hint="Each person needs a different PIN">
                  <Input inputMode="numeric" maxLength={4} value={f.pin} onChange={(e) => set("pin", e.target.value.replace(/\D/g, ""))} required />
                </Field>
              )}
            </div>
            {f.role !== "owner" && (
              <Field label="Outlets">
                <div className="chips">
                  {outlets.map((o) => (
                    <Toggle key={o.id} label={o.name} checked={f.outlet_ids.includes(o.id)}
                      onChange={(on) => set("outlet_ids", on ? [...f.outlet_ids, o.id] : f.outlet_ids.filter((x) => x !== o.id))} />
                  ))}
                </div>
              </Field>
            )}
            {editing.id && <Toggle checked={f.is_active} onChange={(v) => set("is_active", v)} label="Active (can sign in)" />}
            <ErrorText error={error} />
            <div className="row">
              <Button disabled={busy}>Save</Button>
              <Button type="button" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
            </div>
          </form>
        </Card>
      )}

      {pinFor && <PinDialog user={pinFor} onDone={() => { setPinFor(null); load(); }} />}

      <Card>
        <table className="table">
          <thead><tr><th>Name</th><th>Role</th><th>Sign-in</th><th>Outlets</th><th /></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className={u.is_active ? "" : "inactive"}>
                <td>{u.name}</td>
                <td>{ROLE_LABEL[u.role]}</td>
                <td>{[u.phone, u.email].filter(Boolean).join(" · ") || (u.has_pin ? "PIN" : "—")}</td>
                <td>{u.role === "owner" ? "All" : outletNames(u.outlet_ids) || "—"}</td>
                <td className="actions">
                  <button className="link" onClick={() => setEditing({ id: u.id, form: { name: u.name, role: u.role, email: u.email || "", phone: u.phone || "", outlet_ids: u.outlet_ids, pin: "", is_active: u.is_active } })}>Edit</button>
                  <button className="link" onClick={() => setPinFor(u)}>{u.has_pin ? "Change PIN" : "Set PIN"}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function PinDialog({ user, onDone }: { user: User; onDone: () => void }) {
  const [pin, setPin] = useState("");
  const { busy, error, run } = useAction();
  return (
    <Card title={`PIN for ${user.name}`}>
      <form className="row" onSubmit={(e) => {
        e.preventDefault();
        run(async () => { await api(`/users/${user.id}/pin`, { method: "PUT", body: { pin } }); onDone(); });
      }}>
        <Input inputMode="numeric" maxLength={4} value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} placeholder="4 digits" autoFocus />
        <Button disabled={busy || pin.length !== 4}>Save PIN</Button>
        <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>
      </form>
      <ErrorText error={error} />
    </Card>
  );
}
