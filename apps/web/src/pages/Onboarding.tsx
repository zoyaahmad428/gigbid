import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Persona, Settings } from "../api";
import { useAuth } from "../App";
import { useApi } from "../hooks";
import { ErrorNote, money, Spinner } from "../ui";

const STEPS = ["Portfolio", "Rates", "Autonomy", "Marketplace"];

export default function Onboarding() {
  const { user, refresh, signOut } = useAuth();
  const nav = useNavigate();
  const { data: personas } = useApi<Persona[]>("/personas");
  const [step, setStep] = useState(0);
  const [s, setS] = useState<Partial<Settings>>({ autonomy_auto_skip: true, timezone: "Asia/Karachi", ...user?.settings });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (p: Partial<Settings>) => setS((x) => ({ ...x, ...p }));

  function pick(p: Persona) {
    set({ persona: p.persona, name: s.name || p.name, headline: p.headline, hourly_rate: p.hourly_rate,
          min_fixed_budget: p.min_fixed_budget, weekly_connects_budget: p.weekly_connects_budget,
          marketplace_token: p.default_token });
  }

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      await api("/me/settings", { method: "PUT", body: s });
      await refresh();
      nav("/");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!personas) return <Spinner />;
  const chosen = personas.find((p) => p.persona === s.persona);
  const canNext = [!!s.persona, !!s.name && (s.hourly_rate ?? 0) > 0, true, !!s.marketplace_token][step];

  return (
    <div className="auth">
      <div className="card onboard">
        <div className="row between">
          <div className="brand"><span className="logo">G</span> Set up GigBid</div>
          <button className="link" onClick={signOut}>Sign out</button>
        </div>
        <ol className="steps">
          {STEPS.map((t, i) => (
            <li key={t} className={i === step ? "now" : i < step ? "done" : ""}>{t}</li>
          ))}
        </ol>

        {step === 0 && (
          <>
            <p className="muted">
              Choose the portfolio the agents will use as evidence. Every claim in a proposal must point to an item in it.
            </p>
            <div className="persona-grid">
              {personas.map((p) => (
                <button key={p.persona} className={`persona ${s.persona === p.persona ? "chosen" : ""}`} onClick={() => pick(p)}>
                  <strong>{p.name}</strong>
                  <span className="muted small">{p.category}</span>
                  <span>{p.headline}</span>
                  <span className="tags">{p.skills.slice(0, 8).map((k) => <em key={k}>{k}</em>)}</span>
                  <span className="muted small">Doesn't do: {p.does_not_do.join(", ") || "—"}</span>
                </button>
              ))}
            </div>
          </>
        )}

        {step === 1 && (
          <div className="form-grid">
            <label>Name shown in proposals<input value={s.name ?? ""} onChange={(e) => set({ name: e.target.value })} /></label>
            <label>Headline<input value={s.headline ?? ""} onChange={(e) => set({ headline: e.target.value })} /></label>
            <label>
              Hourly rate (USD)
              <input type="number" min={1} value={s.hourly_rate ?? ""} onChange={(e) => set({ hourly_rate: +e.target.value })} />
              <span className="hint">Never bid below this. Jobs paying under 60% of it are skipped.</span>
            </label>
            <label>
              Smallest fixed-price job (USD)
              <input type="number" min={0} value={s.min_fixed_budget ?? ""} onChange={(e) => set({ min_fixed_budget: +e.target.value })} />
            </label>
            {chosen && (
              <p className="hint wide">
                Suggested from market data for {chosen.category}: {money(chosen.hourly_rate)}/hr, minimum {money(chosen.min_fixed_budget)}.
              </p>
            )}
          </div>
        )}

        {step === 2 && (
          <div className="form-grid">
            <label className="check wide">
              <input type="checkbox" checked={!!s.autonomy_auto_skip} onChange={(e) => set({ autonomy_auto_skip: e.target.checked })} />
              <span>
                <strong>Skip clear misfits and scams without asking me</strong>
                <span className="hint">Skipping costs nothing and sends nothing. You can still open a skipped job and choose “bid anyway”.</span>
              </span>
            </label>
            <label className="check wide">
              <input type="checkbox" checked={!!s.auto_scout} onChange={(e) => set({ auto_scout: e.target.checked })} />
              <span>
                <strong>Look for new jobs for me every 30 minutes</strong>
                <span className="hint">GigBid analyses up to two new jobs in your category each time and drafts proposals for good fits. Drafts wait in Approvals.</span>
              </span>
            </label>
            <div className="note wide">
              Proposals and client messages are <strong>always</strong> shown to you first. GigBid cannot send
              anything or spend Connects without your approval, and this cannot be turned off.
            </div>
            <label>
              Weekly Connects budget
              <input type="number" min={0} value={s.weekly_connects_budget ?? 100} onChange={(e) => set({ weekly_connects_budget: +e.target.value })} />
            </label>
            <label>Time zone<input value={s.timezone ?? ""} onChange={(e) => set({ timezone: e.target.value })} /></label>
          </div>
        )}

        {step === 3 && (
          <div className="form-grid">
            <label className="wide">
              Marketplace token
              <input value={s.marketplace_token ?? ""} onChange={(e) => set({ marketplace_token: e.target.value })} />
              <span className="hint">
                The GigSim account for this portfolio. It is checked against the marketplace when you finish.
              </span>
            </label>
          </div>
        )}

        <ErrorNote error={error} />
        <div className="row between">
          <button disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>
          {step < STEPS.length - 1 ? (
            <button className="primary" disabled={!canNext} onClick={() => setStep(step + 1)}>Next</button>
          ) : (
            <button className="primary" disabled={!canNext || busy} onClick={finish}>{busy ? "Checking…" : "Connect and finish"}</button>
          )}
        </div>
      </div>
    </div>
  );
}
