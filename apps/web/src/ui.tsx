import { ReactNode } from "react";

const STATUS_TONE: Record<string, string> = {
  awaiting_approval: "warn",
  running: "info",
  submitted: "good",
  replied: "good",
  interview: "good",
  hired: "good",
  viewed: "info",
  watching: "muted",
  skipped: "muted",
  rejected: "muted",
  lost: "muted",
  withdrawn: "muted",
  draft: "muted",
  blocked: "bad",
  degraded: "bad",
  dead_letter: "bad",
  failed: "bad",
  interrupted: "bad",
  needs_attention: "bad",
  proven: "good",
  partial: "warn",
  missing: "bad",
  supported: "good",
  partly_supported: "warn",
  unsupported: "bad",
  not_a_claim: "muted",
  pass: "good",
  revise: "warn",
};

const LABEL: Record<string, string> = {
  awaiting_approval: "needs your approval",
  dead_letter: "failed",
  needs_attention: "needs attention",
  partly_supported: "partly supported",
  not_a_claim: "not a claim",
};

export function Badge({ status, children }: { status: string; children?: ReactNode }) {
  return <span className={`badge ${STATUS_TONE[status] ?? "muted"}`}>{children ?? LABEL[status] ?? status.replace(/_/g, " ")}</span>;
}

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h3>{title}</h3>}
          {actions && <div className="row gap-s">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value ?? "—"}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="note bad" role="alert">{error}</div> : null;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="spinner" role="status">
      <span className="dot" />
      {label}
    </div>
  );
}

/** Horizontal bars for small categorical counts. */
export function Bars({ rows, format = (n: number) => String(n) }: { rows: [string, number][]; format?: (n: number) => string }) {
  const max = Math.max(1, ...rows.map((r) => r[1]));
  if (!rows.length) return <Empty>No data yet.</Empty>;
  return (
    <div className="bars">
      {rows.map(([k, v]) => (
        <div className="bar-row" key={k}>
          <div className="bar-label" title={k}>{k}</div>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(100 * v) / max}%` }} />
          </div>
          <div className="bar-value">{format(v)}</div>
        </div>
      ))}
    </div>
  );
}

export function Meter({ value, max = 1, tone }: { value: number | null | undefined; max?: number; tone?: "good" | "warn" | "bad" }) {
  if (value == null) return <span className="muted">—</span>;
  const pct = Math.max(0, Math.min(100, (100 * value) / max));
  return (
    <span className="meter" title={value.toFixed(2)}>
      <span className={`meter-fill ${tone ?? ""}`} style={{ width: `${pct}%` }} />
      <span className="meter-num">{value.toFixed(2)}</span>
    </span>
  );
}

export function money(n: number | null | undefined, type?: string) {
  if (n == null) return "—";
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}${type === "hourly" ? "/hr" : ""}`;
}

export function budget(b?: { type: string; min: number | null; max: number | null }) {
  if (!b || (b.min == null && b.max == null)) return `${b?.type ?? "budget"} · not stated`;
  const r = b.min != null && b.max != null && b.min !== b.max ? `${money(b.min)}–${money(b.max)}` : money(b.max ?? b.min);
  return `${r}${b.type === "hourly" ? "/hr" : " fixed"}`;
}

/** Time since t; `now` lets job dates be measured against the marketplace's simulated clock. */
export function ago(t: number | string, now: number = Date.now()) {
  const ms = typeof t === "number" ? t * 1000 : Date.parse(t);
  const s = Math.max(0, (now - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
