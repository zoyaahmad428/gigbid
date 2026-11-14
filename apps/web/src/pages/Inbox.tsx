import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, enc, Job, Persona } from "../api";
import { useAuth } from "../App";
import { useApi, usePoll } from "../hooks";
import { ago, Badge, budget, Empty, ErrorNote, Spinner } from "../ui";

interface Page {
  jobs: Job[];
  total: number;
  next_cursor: number | null;
  sim_time: string;
}

export default function Inbox() {
  const { user } = useAuth();
  const nav = useNavigate();
  const { data: personas } = useApi<Persona[]>("/personas");
  const me = personas?.find((p) => p.persona === user?.settings.persona);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [mine, setMine] = useState(true);
  const [type, setType] = useState("");
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState<string | null>(null);

  const params = useMemo(() => {
    const p = new URLSearchParams({ limit: "20" });
    if (query) p.set("q", query);
    if (mine && me) p.set("category", me.category);
    if (type) p.set("budget_type", type);
    return p;
  }, [query, mine, me, type]);

  const load = useCallback(async (cursor = 0) => {
    if (!personas) return;
    setLoading(true);
    try {
      const p = new URLSearchParams(params);
      p.set("cursor", String(cursor));
      const d = await api<Page>(`/jobs?${p}`);
      setPage((old) => (cursor && old ? { ...d, jobs: [...old.jobs, ...d.jobs] } : d));
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [params, personas]);

  useEffect(() => {
    load(0);
  }, [load]);

  // refresh run badges while something is running
  const anyRunning = !!page?.jobs.some((j) => ["running", "queued"].includes(j.run?.status ?? ""));
  usePoll(async () => {
    if (!page) return;
    const p = new URLSearchParams(params);
    p.set("limit", String(Math.min(50, page.jobs.length)));
    const d = await api<Page>(`/jobs?${p}`).catch(() => null);
    if (d) setPage((old) => old && { ...old, jobs: old.jobs.map((j) => d.jobs.find((x) => x.job_id === j.job_id) ?? j) });
  }, 3000, anyRunning);

  async function analyse(j: Job) {
    setStarting(j.job_id);
    try {
      const r = await api(`/jobs/${j.job_id}/run`, { body: {} });
      nav(`/runs/${enc(r.thread_id)}`);
    } catch (e: any) {
      setError(e.message);
      setStarting(null);
    }
  }

  const now = page ? Date.parse(page.sim_time) : Date.now();
  const skills = new Set((me?.skills ?? []).map((s) => s.toLowerCase()));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Job inbox</h1>
          <p className="muted">
            Open jobs on the marketplace. “Analyse” runs the agents: they check fit and risk, price it and draft a
            proposal, then stop for your approval.
          </p>
        </div>
      </div>
      <form className="filters" onSubmit={(e: FormEvent) => { e.preventDefault(); setQuery(q.trim()); }}>
        <input placeholder="Search titles and descriptions" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search jobs" />
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Budget type">
          <option value="">Fixed and hourly</option>
          <option value="fixed">Fixed price</option>
          <option value="hourly">Hourly</option>
        </select>
        <label className="check inline">
          <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />
          Only {me?.category ?? "my category"}
        </label>
        <button>Search</button>
      </form>
      <ErrorNote error={error} />
      {!page && loading && <Spinner />}
      {page && (
        <>
          <p className="muted small">{page.total} open jobs</p>
          {page.jobs.length === 0 && <Empty>No open jobs match these filters.</Empty>}
          <ul className="job-list">
            {page.jobs.map((j) => (
              <li key={j.job_id} className="job-row">
                <div className="job-main">
                  <Link to={`/jobs/${j.job_id}`} className="job-title">{j.title}</Link>
                  <div className="job-meta">
                    <span>{budget(j.budget)}</span>
                    <span>{j.category}</span>
                    <span>{j.client_country}</span>
                    <span>posted {ago(j.posted_at, now)}</span>
                    <span>{j.connects_cost} Connects</span>
                  </div>
                  <p className="job-desc">{j.description.slice(0, 220)}{j.description.length > 220 ? "…" : ""}</p>
                  <div className="tags">
                    {j.skills.map((s) => <em key={s} className={skills.has(s.toLowerCase()) ? "match" : ""}>{s}</em>)}
                  </div>
                </div>
                <div className="job-side">
                  {j.run ? (
                    <>
                      <Badge status={j.run.status} />
                      {j.run.summary?.fit != null && <span className="muted small">fit {j.run.summary.fit.toFixed(2)}</span>}
                      <Link className="button" to={`/runs/${enc(j.run.thread_id)}`}>
                        {j.run.status === "awaiting_approval" ? "Review" : "Open"}
                      </Link>
                    </>
                  ) : (
                    <button className="primary" disabled={starting === j.job_id} onClick={() => analyse(j)}>
                      {starting === j.job_id ? "Starting…" : "Analyse"}
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {page.next_cursor != null && (
            <button disabled={loading} onClick={() => load(page.next_cursor!)}>{loading ? "Loading…" : "Load more"}</button>
          )}
        </>
      )}
    </div>
  );
}
