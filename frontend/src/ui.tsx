import { Children, isValidElement, useState, type ReactElement, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

export function Button({ variant = "primary", className = "", ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" }) {
  return <button className={`btn btn-${variant} ${className}`} {...props} />;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

/** A labelled group of checkboxes or toggles. Not a <label>: clicking its heading must not tick the first box. */
export function FieldGroup({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <fieldset className="field field-group">
      <legend className="field-label">{label}</legend>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </fieldset>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className="input" {...props} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className="input" {...props} />;
}

export function ErrorText({ error }: { error: string | null }) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

export function Card({ title, actions, children }: { title?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

/** Wraps an async action with busy + error state. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      return undefined;
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, setError, run };
}

export function FoodDot({ type }: { type: "veg" | "non_veg" | "egg" }) {
  const label = type === "veg" ? "Veg" : type === "egg" ? "Egg" : "Non-veg";
  return <span className={`food-dot food-${type}`} title={label} aria-label={label} />;
}

/** One section of a <Tabs> page. */
export function Tab({ children }: { label: string; children: ReactNode }) { return <>{children}</>; }

/** Shows one section of a long page at a time, with the section names as tabs. Remembers the last tab per page. */
export function Tabs({ id, children }: { id: string; children: ReactNode }) {
  const tabs = Children.toArray(children).filter(isValidElement) as ReactElement<{ label: string; children: ReactNode }>[];
  const key = `wise.tab.${id}`;
  const [cur, setCur] = useState(() => { try { return sessionStorage.getItem(key) || ""; } catch { return ""; } });
  if (tabs.length === 0) return null;
  if (tabs.length === 1) return <>{tabs[0]}</>;
  const idx = Math.max(0, tabs.findIndex((t) => t.props.label === cur));
  const pick = (label: string) => { setCur(label); try { sessionStorage.setItem(key, label); } catch { /* private mode */ } };
  return (
    <>
      <div className="subtabs" role="tablist">
        {tabs.map((t, i) => (
          <button key={t.props.label} type="button" role="tab" aria-selected={i === idx} className={i === idx ? "on" : ""} onClick={() => pick(t.props.label)}>{t.props.label}</button>
        ))}
      </div>
      <div className="tab-panel" role="tabpanel" aria-label={tabs[idx].props.label}>{tabs[idx]}</div>
    </>
  );
}
