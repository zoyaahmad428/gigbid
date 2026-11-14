import { Fragment, useState } from "react";
import { api, Settings } from "../api";
import { useAuth } from "../App";
import { useApi } from "../hooks";
import { Card, Empty, ErrorNote, Spinner } from "../ui";

export default function SettingsPage() {
  const { user, refresh } = useAuth();
  const [s, setS] = useState<Partial<Settings>>({ ...user!.settings });
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mem = useApi<{ style_rules: any[]; outcome_lessons: Record<string, any[]> }>("/memory");
  const cfg = useApi<any>("/config");
  const set = (p: Partial<Settings>) => { setS((x) => ({ ...x, ...p })); setSaved(false); };

  async function save() {
    setError(null);
    try {
      await api("/me/settings", { method: "PUT", body: s });
      await refresh();
      setSaved(true);
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function forget(path: string) {
    await api(path, { method: "DELETE" });
    mem.reload();
  }

  return (
    <div className="page">
      <div className="page-head"><div><h1>Settings</h1></div></div>
      <div className="two-col">
        <Card title="Profile and rates">
          <div className="form-grid">
            <label>Name<input value={s.name ?? ""} onChange={(e) => set({ name: e.target.value })} /></label>
            <label>Headline<input value={s.headline ?? ""} onChange={(e) => set({ headline: e.target.value })} /></label>
            <label>Hourly rate (USD)<input type="number" value={s.hourly_rate ?? ""} onChange={(e) => set({ hourly_rate: +e.target.value })} /></label>
            <label>Smallest fixed job (USD)<input type="number" value={s.min_fixed_budget ?? ""} onChange={(e) => set({ min_fixed_budget: +e.target.value })} /></label>
            <label>Weekly Connects budget<input type="number" value={s.weekly_connects_budget ?? 100} onChange={(e) => set({ weekly_connects_budget: +e.target.value })} /></label>
            <label>Time zone<input value={s.timezone ?? ""} onChange={(e) => set({ timezone: e.target.value })} /></label>
            <label className="check wide">
              <input type="checkbox" checked={!!s.autonomy_auto_skip} onChange={(e) => set({ autonomy_auto_skip: e.target.checked })} />
              <span>Skip clear misfits and scams without asking me <span className="hint">Sending always needs your approval.</span></span>
            </label>
            <label className="wide">Marketplace token<input value={s.marketplace_token ?? ""} onChange={(e) => set({ marketplace_token: e.target.value })} /></label>
            <p className="hint wide">Portfolio: <strong>{s.persona}</strong> (chosen at onboarding).</p>
          </div>
          <ErrorNote error={error} />
          <div className="row gap-s">
            <button className="primary" onClick={save}>Save</button>
            {saved && <span className="good-text small">Saved and marketplace token checked.</span>}
          </div>
        </Card>

        <Card title="What the agents have learned about you">
          <p className="hint">
            Learned from your edits and from how clients responded. A rule is used only after it repeats, and
            you can delete anything here; deleted style rules are not learned again.
          </p>
          {!mem.data && <Spinner />}
          {mem.data && (
            <>
              <h4>Writing style (from your edits)</h4>
              {!mem.data.style_rules.length && <Empty>No style rules yet. They appear after you edit drafts.</Empty>}
              <ul className="memory">
                {mem.data.style_rules.map((r) => (
                  <li key={r.rule}>
                    <span>{r.text ?? r.rule} <span className="muted small">(<code>{r.rule}</code>, from {r.support} edits)</span></span>
                    <button className="link" onClick={() => forget(`/memory/style/${encodeURIComponent(r.rule)}`)}>Forget</button>
                  </li>
                ))}
              </ul>
              <h4>Outcome lessons (from client responses)</h4>
              {!Object.keys(mem.data.outcome_lessons).length && <Empty>No lessons yet. They appear after clients respond.</Empty>}
              <ul className="memory">
                {Object.entries(mem.data.outcome_lessons).map(([cat, ls]) => (
                  <li key={cat}>
                    <span><strong>{cat}</strong>: {ls.map((l: any) => l.text ?? l.lesson ?? JSON.stringify(l)).join("; ")}</span>
                    <button className="link" onClick={() => forget(`/memory/outcome/${encodeURIComponent(cat)}`)}>Forget</button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>

        <Card title="Agent thresholds (read only)" className="wide">
          <p className="hint">From agent/config.yaml. Fit thresholds were calibrated on the development set (evals/results/fit_calibration.json).</p>
          {cfg.data ? (
            <div className="cfg">
              {["thresholds", "pricing", "budgets"].map((k) => cfg.data[k] && (
                <div key={k}>
                  <h4>{k}</h4>
                  <dl className="kv small">
                    {Object.entries(cfg.data[k]).map(([kk, vv]) => (
                      <Fragment key={kk}><dt>{kk.replace(/_/g, " ")}</dt><dd>{typeof vv === "object" ? JSON.stringify(vv) : String(vv)}</dd></Fragment>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
          ) : <Spinner />}
        </Card>
      </div>
    </div>
  );
}
