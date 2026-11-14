import { Link } from "react-router-dom";
import { enc, Run } from "../api";
import { useApi, usePoll } from "../hooks";
import { Badge, Empty, ErrorNote, money, Spinner } from "../ui";

const LANES: [string, string, (s: string) => boolean][] = [
  ["Analysing", "agents at work", (s) => s === "running" || s === "queued"],
  ["Needs you", "waiting for approval", (s) => s === "awaiting_approval"],
  ["Watching", "fit or budget was borderline", (s) => s === "watching"],
  ["Sent", "submitted, no reply yet", (s) => ["submitted", "viewed"].includes(s)],
  ["Talking", "client replied or invited", (s) => ["replied", "interview"].includes(s)],
  ["Hired", "", (s) => s === "hired"],
  ["Closed", "lost, skipped, rejected, withdrawn", (s) => ["lost", "skipped", "rejected", "withdrawn", "blocked"].includes(s)],
];

export default function Pipeline() {
  const runs = useApi<{ runs: Run[] }>("/runs?kind=bid");
  const props = useApi<{ proposals: any[] }>("/proposals");
  usePoll(() => { runs.reload(); props.reload(); }, 8000);

  if (runs.error || props.error) return <ErrorNote error={runs.error ?? props.error} />;
  if (!runs.data || !props.data) return <Spinner />;

  // one card per job: the marketplace status wins once a proposal exists
  const byJob = new Map<string, any>();
  for (const r of runs.data.runs) byJob.set(r.job_id!, { job_id: r.job_id, title: r.title, status: r.status, run: r });
  for (const p of props.data.proposals) {
    const cur = byJob.get(p.job_id) ?? { job_id: p.job_id, title: p.job_id };
    if (p.status !== "draft") byJob.set(p.job_id, { ...cur, status: p.status, proposal: p });
  }
  const cards = [...byJob.values()];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Pipeline</h1>
          <p className="muted">Every job from analysis to outcome. Client replies and hires come from the marketplace.</p>
        </div>
      </div>
      {!cards.length && <Empty>Nothing in the pipeline yet. Analyse a job from the inbox.</Empty>}
      <div className="lanes">
        {LANES.map(([name, hint, test]) => {
          const xs = cards.filter((c) => test(c.status));
          return (
            <div className="lane" key={name}>
              <div className="lane-head"><strong>{name}</strong> <span className="muted small">{xs.length}</span></div>
              {hint && <div className="muted small lane-hint">{hint}</div>}
              {xs.map((c) => (
                <Link key={c.job_id} className="lane-card" to={c.run ? `/runs/${enc(c.run.thread_id)}` : `/messages/${c.proposal?.proposal_id}`}>
                  <div className="clip2">{c.title}</div>
                  <div className="row gap-s wrap small">
                    <Badge status={c.status} />
                    {c.proposal?.bid_amount != null && <span>{money(c.proposal.bid_amount, c.proposal.bid_type)}</span>}
                    {c.run?.summary?.fit != null && <span className="muted">fit {c.run.summary.fit.toFixed(2)}</span>}
                  </div>
                  {c.proposal && ["replied", "interview"].includes(c.status) && <span className="small">Open messages →</span>}
                </Link>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}
