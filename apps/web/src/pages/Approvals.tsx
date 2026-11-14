import { useState } from "react";
import { Link } from "react-router-dom";
import { enc, Run } from "../api";
import { useApi, usePoll } from "../hooks";
import { ago, Badge, Empty, ErrorNote, money, Spinner } from "../ui";

/** Pending approvals (default) or every agent run (all). Both are rebuilt from the server on load. */
export default function Approvals({ all = false }: { all?: boolean }) {
  const [filter, setFilter] = useState("");
  const path = all ? `/runs${filter ? `?status=${filter}` : ""}` : "/approvals";
  const { data, error, reload } = useApi<{ runs: Run[] }>(path);
  usePoll(reload, 4000, !!data?.runs.some((r) => r.status === "running"));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{all ? "Agent runs" : "Waiting for your approval"}</h1>
          <p className="muted">
            {all
              ? "Every job the agents analysed and every reply they drafted, newest first."
              : "Proposals and replies the agents have prepared. Nothing here has been sent."}
          </p>
        </div>
        {all && (
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter by status">
            <option value="">All statuses</option>
            {["running", "awaiting_approval", "submitted", "replied", "watching", "skipped", "rejected", "blocked", "degraded", "dead_letter", "interrupted", "failed"].map((s) => (
              <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
            ))}
          </select>
        )}
      </div>
      <ErrorNote error={error} />
      {!data && <Spinner />}
      {data && !data.runs.length && <Empty>{all ? "No runs yet. Start one from the inbox." : "Nothing is waiting for you."}</Empty>}
      {data && data.runs.length > 0 && (
        <div className="table-wrap">
          <table className="list">
            <thead>
              <tr><th>Job</th><th>Type</th><th>Status</th><th>Fit</th><th>Bid</th><th>Cost</th><th>Updated</th></tr>
            </thead>
            <tbody>
              {data.runs.map((r) => (
                <tr key={r.thread_id}>
                  <td><Link to={`/runs/${enc(r.thread_id)}`}>{r.title ?? r.job_id}</Link>{r.reason && <div className="small muted clip">{r.reason}</div>}</td>
                  <td>{r.kind === "bid" ? "proposal" : "reply"}</td>
                  <td><Badge status={r.status} /></td>
                  <td>{r.summary.fit != null ? r.summary.fit.toFixed(2) : ""}</td>
                  <td>{r.summary.bid != null ? money(r.summary.bid, r.summary.bid_type) : ""}</td>
                  <td>{r.summary.trace?.cost_usd != null ? `$${r.summary.trace.cost_usd.toFixed(4)}` : ""}</td>
                  <td className="muted small">{ago(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
