import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Button, Card, ErrorText, useAction } from "../ui";

interface RoleRow { role: string; label: string; perms: string[]; default: string[]; editable: boolean; changed: boolean }
interface PermRow { key: string; group: string; label: string; help: string; sensitive: boolean }
interface View { roles: RoleRow[]; permissions: PermRow[]; can_edit: boolean }

/** Who can do what: the owner ticks what each role may see and do. */
export default function Access() {
  const [v, setV] = useState<View | null>(null);
  const [draft, setDraft] = useState<Record<string, string[]>>({});
  const [saved, setSaved] = useState<string | null>(null);
  // Phones show one role at a time; the role buttons switch which column is shown.
  const [focus, setFocus] = useState<string>("");
  const { busy, error, run } = useAction();
  const load = (x: View) => { setV(x); setDraft(Object.fromEntries(x.roles.map((r) => [r.role, r.perms]))); };
  useEffect(() => { api<View>("/tenant/roles").then(load); }, []);
  const groups = useMemo(() => {
    const g = new Map<string, PermRow[]>();
    (v?.permissions || []).forEach((p) => g.set(p.group, [...(g.get(p.group) || []), p]));
    return [...g.entries()];
  }, [v]);
  if (!v) return <p className="muted">Loading…</p>;
  const dirty = v.roles.filter((r) => r.editable && [...draft[r.role]].sort().join() !== [...r.perms].sort().join());
  const toggle = (role: string, key: string) => { setSaved(null); setDraft((d) => ({ ...d, [role]: d[role].includes(key) ? d[role].filter((x) => x !== key) : [...d[role], key] })); };
  const save = () => run(async () => {
    let x: View | null = null;
    for (const r of dirty) x = await api<View>(`/tenant/roles/${r.role}`, { method: "PUT", body: { perms: draft[r.role] } });
    if (x) load(x);
    setSaved(`Saved. Staff see the change the next time their screen loads (within a few seconds).`);
  });
  const shownRole = focus || v.roles.find((r) => r.editable)?.role || v.roles[0]?.role;
  const reset = (r: RoleRow) => { setSaved(null); setDraft((d) => ({ ...d, [r.role]: r.default })); };
  return (
    <div className="stack">
      <h1>Who can do what</h1>
      <p className="muted">Tick what each role may see and do. The owner always has everything. Items marked <span className="sens">careful</span> show money or change settings: think twice before giving them to counter staff.{!v.can_edit && " Only the owner can change this."}</p>
      <Card>
        <div className="role-pick" role="group" aria-label="Role to show">
          {v.roles.map((r) => <button key={r.role} type="button" className={`choice ${shownRole === r.role ? "on" : ""}`} aria-pressed={shownRole === r.role} onClick={() => setFocus(r.role)}>{r.label}</button>)}
        </div>
        <div className="access-wrap">
          <table className="table access">
            <thead>
              <tr><th>Permission</th>{v.roles.map((r) => <th key={r.role} className={`c ${shownRole === r.role ? "" : "other"}`}>{r.label}{r.editable && [...draft[r.role]].sort().join() !== [...r.default].sort().join() && <div><button className="link small" onClick={() => reset(r)} disabled={!v.can_edit}>Default</button></div>}</th>)}</tr>
            </thead>
            <tbody>
              {groups.map(([g, list]) => [
                <tr key={g} className="group"><td colSpan={v.roles.length + 1}>{g}</td></tr>,
                ...list.map((p) => (
                  <tr key={p.key}>
                    <td><strong>{p.label}</strong>{p.sensitive && <span className="sens">careful</span>}{p.help && <div className="muted small">{p.help}</div>}</td>
                    {v.roles.map((r) => {
                      const on = draft[r.role].includes(p.key), def = r.default.includes(p.key);
                      return (
                        <td key={r.role} className={`c ${on !== def ? "changed" : ""} ${shownRole === r.role ? "" : "other"}`}>
                          <input type="checkbox" checked={on} disabled={!r.editable || !v.can_edit} onChange={() => toggle(r.role, p.key)}
                            aria-label={`${r.label}: ${p.label}`} />
                        </td>
                      );
                    })}
                  </tr>
                )),
              ])}
            </tbody>
          </table>
        </div>
        <ErrorText error={error} />
        {saved && <p className="ok">{saved}</p>}
        {v.can_edit && <div className="row"><Button disabled={busy || !dirty.length} onClick={save}>Save{dirty.length ? ` (${dirty.map((r) => r.label).join(", ")})` : ""}</Button>
          {dirty.length > 0 && <Button variant="ghost" onClick={() => load(v)}>Undo changes</Button>}</div>}
        <p className="muted small">A highlighted box differs from Wise's default. Every change is kept in the audit log.</p>
      </Card>
    </div>
  );
}
