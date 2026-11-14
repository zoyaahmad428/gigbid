import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, enc } from "../api";
import { useApi, usePoll } from "../hooks";
import { Badge, Card, Empty, ErrorNote, money, Spinner } from "../ui";

export default function Messages() {
  const { proposalId } = useParams();
  const list = useApi<{ proposals: any[] }>("/proposals");
  usePoll(list.reload, 10000);
  const sent = (list.data?.proposals ?? []).filter((p) => p.status !== "draft");

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Messages</h1>
          <p className="muted">Client replies to your proposals. The agents draft an answer; you approve it before it is sent.</p>
        </div>
      </div>
      <ErrorNote error={list.error} />
      {!list.data && <Spinner />}
      {list.data && (
        <div className="inbox-split">
          <ul className="convo-list">
            {!sent.length && <Empty>No submitted proposals yet.</Empty>}
            {sent.map((p) => (
              <li key={p.proposal_id}>
                <Link to={`/messages/${p.proposal_id}`} className={p.proposal_id === proposalId ? "active" : ""}>
                  <div className="clip">{p.run?.title ?? p.job_id}</div>
                  <div className="row gap-s small">
                    <Badge status={p.status} />
                    {p.reply_run?.status === "awaiting_approval" && <Badge status="awaiting_approval">reply ready</Badge>}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
          <div>{proposalId ? <Thread key={proposalId} id={proposalId} /> : <Empty>Pick a conversation.</Empty>}</div>
        </div>
      )}
    </div>
  );
}

function Thread({ id }: { id: string }) {
  const nav = useNavigate();
  const { data: p, error, reload } = useApi<any>(`/proposals/${id}`);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  usePoll(reload, 8000);

  async function draft() {
    setBusy(true);
    try {
      const r = await api(`/proposals/${id}/reply`, { body: {} });
      nav(`/runs/${enc(r.thread_id)}`);
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  }

  if (error) return <ErrorNote error={error} />;
  if (!p) return <Spinner />;
  const msgs: any[] = p.messages ?? [];
  const lastClient = msgs.length > 0 && msgs[msgs.length - 1].sender === "client";
  const openRun = p.reply_runs?.find((r: any) => ["awaiting_approval", "running", "queued"].includes(r.status));

  return (
    <Card title={<>Proposal <code>{p.proposal_id}</code></>} actions={<Badge status={p.status} />}>
      <p className="small muted">Bid {money(p.bid_amount, p.bid_type)} · {p.connects_spent ?? 0} Connects · submitted {p.submitted_at?.slice(0, 16).replace("T", " ")}</p>
      <details>
        <summary className="small">Your cover letter</summary>
        <p className="prewrap small">{p.cover_letter}</p>
      </details>
      <div className="thread">
        {!msgs.length && <Empty>No messages yet. Clients reply on the marketplace's clock; check back later.</Empty>}
        {msgs.map((m) => (
          <div key={m.message_id} className={`msg ${m.sender}`}>
            <div className="small muted">{m.sender === "client" ? "Client" : "You"} · {m.sent_at?.slice(0, 16).replace("T", " ")}</div>
            <div className="prewrap">{m.text}</div>
          </div>
        ))}
      </div>
      <ErrorNote error={err} />
      <div className="row gap-s">
        {openRun ? (
          <Link className="button primary" to={`/runs/${enc(openRun.thread_id)}`}>
            {openRun.status !== "awaiting_approval" ? "Reply being drafted…" : "Review drafted reply"}
          </Link>
        ) : (
          lastClient && <button className="primary" disabled={busy} onClick={draft}>{busy ? "Starting…" : "Draft a reply"}</button>
        )}
      </div>
      {!!p.reply_runs?.length && (
        <ul className="plain small">
          {p.reply_runs.map((r: any) => (
            <li key={r.thread_id}><Link to={`/runs/${enc(r.thread_id)}`}>reply run</Link> <Badge status={r.status} /> {r.summary?.intent?.replace(/_/g, " ")}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}
