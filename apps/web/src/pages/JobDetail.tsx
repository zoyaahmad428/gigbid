import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, enc, Job, Run } from "../api";
import { useApi } from "../hooks";
import { Badge, budget, Card, ErrorNote, money, Spinner } from "../ui";

export default function JobDetail() {
  const { jobId } = useParams();
  const nav = useNavigate();
  const { data, error } = useApi<{ job: Job; client: any; run: Run | null }>(`/jobs/${jobId}`);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start(force = false) {
    setBusy(true);
    try {
      const r = await api(`/jobs/${jobId}/run`, { body: { force_bid: force } });
      nav(`/runs/${enc(r.thread_id)}`);
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  }

  if (error) return <ErrorNote error={error} />;
  if (!data) return <Spinner />;
  const { job: j, client: c, run } = data;
  const canForce = run && ["skipped", "watching"].includes(run.status);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <Link to="/" className="muted small">← Inbox</Link>
          <h1>{j.title}</h1>
          <div className="job-meta">
            <span>{budget(j.budget)}</span>
            <span>{j.category}</span>
            <span>{j.duration}</span>
            <span>{j.experience_level}</span>
            <span>{j.connects_cost} Connects to apply</span>
          </div>
        </div>
        <div className="row gap-s">
          {run && <Badge status={run.status} />}
          {run ? (
            <>
              <Link className="button primary" to={`/runs/${enc(run.thread_id)}`}>
                {run.status === "awaiting_approval" ? "Review proposal" : "Open run"}
              </Link>
              {canForce && <button disabled={busy} onClick={() => start(true)} title="Override the agents' recommendation">Bid anyway</button>}
            </>
          ) : (
            <button className="primary" disabled={busy} onClick={() => start(false)}>{busy ? "Starting…" : "Analyse and draft"}</button>
          )}
        </div>
      </div>
      <ErrorNote error={err} />
      {run?.reason && <div className="note">{run.reason}</div>}
      <div className="two-col">
        <Card title="Job post">
          <div className="note small muted">
            This text was written by the client. The agents treat it as data, never as instructions.
          </div>
          <p className="prewrap">{j.description}</p>
          <div className="tags">{j.skills.map((s) => <em key={s}>{s}</em>)}</div>
          {!!j.screening_questions?.length && (
            <>
              <h4>Screening questions</h4>
              <ol>{j.screening_questions.map((q) => <li key={q}>{q}</li>)}</ol>
            </>
          )}
        </Card>
        <Card title="Client">
          {c ? (
            <dl className="kv">
              <dt>Country</dt><dd>{c.country}</dd>
              <dt>Payment verified</dt><dd>{c.payment_verified ? "yes" : <span className="bad-text">no</span>}</dd>
              <dt>Total spent</dt><dd>{money(c.total_spent_usd)}</dd>
              <dt>Jobs posted / hires</dt><dd>{c.jobs_posted} / {c.hires}</dd>
              <dt>Rating</dt><dd>{c.rating ?? "no reviews"} {c.feedback_count ? `(${c.feedback_count})` : ""}</dd>
              <dt>Member since</dt><dd>{c.member_since}</dd>
              <dt>Industry</dt><dd>{c.industry}</dd>
            </dl>
          ) : (
            <p className="muted">No client history.</p>
          )}
          {!!c?.past_contracts?.length && (
            <>
              <h4>Recent contracts</h4>
              <ul className="plain small">
                {c.past_contracts.slice(0, 5).map((p: any, i: number) => (
                  <li key={i}>{p.title ?? p.job_title} — {p.rating != null ? `★ ${p.rating}` : ""} {p.feedback ? `“${p.feedback}”` : ""}</li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
