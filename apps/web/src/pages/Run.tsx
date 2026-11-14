import { Fragment, ReactNode, useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, RunEvent } from "../api";
import { useApi, useRunStream } from "../hooks";
import { ago, Badge, Card, Empty, ErrorNote, Meter, money, Spinner, Stat } from "../ui";

const BID_STEPS: [string, string][] = [
  ["load_job", "Load job"],
  ["analyst", "Job Analyst"],
  ["risk_assessor", "Risk Assessor"],
  ["fit_matcher", "Fit Matcher"],
  ["decision", "Decision"],
  ["pricing", "Pricing"],
  ["writer", "Proposal Writer"],
  ["verifier", "Claim Verifier"],
  ["critic", "Critic"],
  ["compliance", "Compliance Guard"],
  ["approval_gate", "Your approval"],
  ["submit", "Submit"],
];
const CONVO_STEPS: [string, string][] = [
  ["read", "Read messages"],
  ["intent", "Classify intent"],
  ["slots", "Find interview slots"],
  ["draft_reply", "Draft reply"],
  ["compliance", "Compliance Guard"],
  ["approval", "Your approval"],
  ["send", "Send"],
];
const RETRYABLE = ["degraded", "dead_letter", "failed", "interrupted", "needs_attention"];
const CITE = /\s*\[((?:ev|rev|prop)_[0-9a-f]+(?:\s*,\s*(?:ev|rev|prop)_[0-9a-f]+)*)\]/g;

type Detail = {
  run: any;
  busy: boolean;
  next: string[];
  pending_approval: any | null;
  values: Record<string, any>;
  evidence: Record<string, { title: string; type: string; text: string; skills?: string[] }>;
  history: any[];
  trace: any[];
  totals: { tokens_in: number; tokens_out: number; cost_usd: number; events: number };
};

export default function RunPage() {
  const threadId = decodeURIComponent(useParams().threadId!);
  const path = `/runs/${encodeURIComponent(threadId)}`;
  const { data: d, error, reload } = useApi<Detail>(path);
  const [nonce, setNonce] = useState(0);
  const [tab, setTab] = useState<string | null>(null);
  const { events, connected } = useRunStream(threadId, nonce, useCallback(() => {
    reload();
    window.dispatchEvent(new Event("gigbid:changed"));
  }, [reload]));
  const [actionError, setActionError] = useState<string | null>(null);

  const liveStatus = useMemo(() => {
    const done = [...events].reverse().find((e) => e.type === "done");
    return done?.status;
  }, [events]);

  async function act(p: string, body: unknown = {}) {
    setActionError(null);
    try {
      await api(`${path}/${p}`, { body });
      setNonce((n) => n + 1);
      await reload();
    } catch (e: any) {
      setActionError(e.message);
      throw e;
    }
  }

  if (error) return <ErrorNote error={error} />;
  if (!d) return <Spinner />;
  const isBid = d.run.kind === "bid";
  const running = d.busy || d.run.status === "running";
  const status = running ? "running" : d.pending_approval ? "awaiting_approval" : liveStatus ?? d.run.status;
  const defaultTab = d.pending_approval || (isBid && d.values.draft) || (!isBid && d.values.reply) ? "work" : "run";
  const current = tab ?? defaultTab;
  const cost = d.totals.cost_usd;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <Link to={isBid ? "/runs" : "/messages"} className="muted small">← {isBid ? "Agent runs" : "Messages"}</Link>
          <h1>{d.run.title ?? d.run.job_id}</h1>
          <div className="row gap-s wrap">
            <Badge status={status} />
            <span className="muted small">{isBid ? "Bid" : "Client reply"} · started {ago(d.run.created_at)}</span>
            {d.run.job_id && isBid && <Link className="small" to={`/jobs/${d.run.job_id}`}>job post</Link>}
            {connected && running && <span className="live">live</span>}
          </div>
        </div>
        <div className="row gap-s">
          {RETRYABLE.includes(status) && !running && (
            <button onClick={() => act("retry").catch(() => {})} title="Continue from the last checkpoint before the failure">Retry</button>
          )}
        </div>
      </div>
      {d.run.reason && !running && <div className={`note ${RETRYABLE.includes(status) ? "bad" : ""}`}>{d.values.status_reason ?? d.run.reason}</div>}
      <ErrorNote error={actionError} />

      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={current === "work"} className={current === "work" ? "on" : ""} onClick={() => setTab("work")}>
          {isBid ? "Proposal" : "Reply"}
          {d.pending_approval && <span className="count">1</span>}
        </button>
        <button role="tab" aria-selected={current === "run"} className={current === "run" ? "on" : ""} onClick={() => setTab("run")}>Agent run</button>
        {isBid && (
          <button role="tab" aria-selected={current === "versions"} className={current === "versions" ? "on" : ""} onClick={() => setTab("versions")}>
            Versions ({d.values.drafts?.length ?? 0})
          </button>
        )}
        <button role="tab" aria-selected={current === "trace"} className={current === "trace" ? "on" : ""} onClick={() => setTab("trace")}>
          Trace ({d.totals.events})
        </button>
      </div>

      {current === "work" && (isBid ? <ProposalStudio d={d} running={running} act={act} /> : <ReplyStudio d={d} running={running} act={act} />)}
      {current === "run" && (
        <RunViewer steps={isBid ? BID_STEPS : CONVO_STEPS} events={events} d={d} running={running} cost={cost} />
      )}
      {current === "versions" && <Versions d={d} />}
      {current === "trace" && <Trace d={d} />}
    </div>
  );
}

// ------------------------------------------------------------------ proposal studio
function ProposalStudio({ d, running, act }: { d: Detail; running: boolean; act: (p: string, b?: unknown) => Promise<void> }) {
  const p = d.pending_approval?.kind === "proposal" ? d.pending_approval : null;
  const v = d.values;
  const draft = p?.draft ?? v.draft;
  const [mode, setMode] = useState<"view" | "edit" | "reject" | "confirm">("view");
  const [text, setText] = useState("");
  const [bid, setBid] = useState<string>("");
  const [reason, setReason] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!draft) {
    if (running) return <Empty>The agents are still working. The draft appears here when it is ready for review.</Empty>;
    return (
      <div className="stack">
        <DecisionCard v={v} />
        <Empty>No proposal was drafted for this job. {v.status_reason}</Empty>
      </div>
    );
  }

  const quote = p?.price_quote ?? v.price_quote;
  const preview = p?.preview ?? v.preview;
  const claims: any[] = p?.claims ?? draft.citations ?? [];
  const send = async (body: any) => {
    setBusy(true);
    try {
      await act("decision", body);
      setMode("view");
    } catch {
      /* shown above */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="studio">
      <div className="stack">
        {p && !running && (
          <div className="approval-bar">
            <div>
              <strong>Ready for your review.</strong> Nothing is sent until you approve.
              {preview && <span className="muted"> Sending costs {preview.connects_cost} Connects (balance after: {preview.connects_balance_after}).</span>}
            </div>
            <div className="row gap-s">
              <button className="primary" onClick={() => setMode("confirm")} disabled={busy}>Approve and send…</button>
              <button onClick={() => { setText(draft.cover_letter); setBid(String(quote?.amount ?? "")); setMode("edit"); }} disabled={busy}>Edit</button>
              <button className="danger" onClick={() => setMode("reject")} disabled={busy}>Reject</button>
            </div>
          </div>
        )}
        {running && p == null && <div className="note">The agents are working on this proposal…</div>}

        {mode === "confirm" && p && (
          <div className="dialog" role="dialog" aria-label="Confirm sending">
            <h3>Send this proposal?</h3>
            <ul className="plain">
              <li>Submits the cover letter{draft.screening_answers?.length ? ` and ${draft.screening_answers.length} screening answer(s)` : ""} below to the client.</li>
              <li>Bid: <strong>{money(quote?.amount, quote?.bid_type)}</strong></li>
              <li>Spends <strong>{preview?.connects_cost} Connects</strong>; balance after: {preview?.connects_balance_after}.</li>
              {preview?.checks?.problems?.length > 0 && <li className="bad-text">Problems: {preview.checks.problems.join("; ")}</li>}
              {preview?.checks?.policy_violations?.length > 0 && <li className="bad-text">Policy: {preview.checks.policy_violations.join("; ")}</li>}
            </ul>
            <div className="row gap-s">
              <button className="primary" disabled={busy} onClick={() => send({ action: "approve" })}>{busy ? "Sending…" : "Yes, send it"}</button>
              <button onClick={() => setMode("view")}>Cancel</button>
            </div>
          </div>
        )}

        {mode === "reject" && (
          <div className="dialog" role="dialog" aria-label="Reject">
            <h3>Reject this proposal</h3>
            <p className="muted small">Nothing is sent. The reason is kept with the run.</p>
            <textarea rows={3} placeholder="Why? (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="row gap-s">
              <button className="danger" disabled={!reason.trim() || busy} onClick={() => send({ action: "reject", reason: reason.trim() })}>Reject</button>
              <button onClick={() => setMode("view")}>Cancel</button>
            </div>
          </div>
        )}

        <Card title="Cover letter" actions={<span className="muted small">revision {draft.revision ?? 0} · {draft.status}</span>}>
          {mode === "edit" ? (
            <div className="stack">
              <p className="hint">
                Keep the [ev_…] markers next to claims you keep. After you save, the edit goes back through the Claim
                Verifier and Compliance Guard, then returns here for approval. Repeated edits teach the writer your style.
              </p>
              <textarea className="letter-edit" rows={16} value={text} onChange={(e) => setText(e.target.value)} />
              <label className="inline">
                Bid ({quote?.bid_type}) $
                <input type="number" min={1} value={bid} onChange={(e) => setBid(e.target.value)} />
              </label>
              <div className="row gap-s">
                <button className="primary" disabled={busy || !text.trim()}
                        onClick={() => send({ action: "edit", edited_text: text !== draft.cover_letter ? text : undefined,
                                              edited_bid: +bid !== quote?.amount ? +bid : undefined })}>
                  Save edit and re-check
                </button>
                <button onClick={() => setMode("view")}>Cancel</button>
              </div>
            </div>
          ) : (
            <Letter text={draft.cover_letter} claims={claims} onPick={setPicked} />
          )}
          <div className="legend small">
            <span><i className="hl supported" /> supported by evidence</span>
            <span><i className="hl partly_supported" /> partly supported</span>
            <span><i className="hl unsupported" /> not supported</span>
            <span className="muted">Click a citation to see the evidence.</span>
          </div>
        </Card>

        {!!draft.screening_answers?.length && (
          <Card title="Screening answers">
            <ol className="answers">
              {draft.screening_answers.map((a: string, i: number) => (
                <li key={i}>
                  {v.job?.screening_questions?.[i] && <div className="muted small">{v.job.screening_questions[i]}</div>}
                  <Letter text={a} claims={claims} onPick={setPicked} />
                </li>
              ))}
            </ol>
          </Card>
        )}

        {picked && <EvidencePanel id={picked} d={d} onClose={() => setPicked(null)} />}
        <ClaimsCard claims={claims} stats={p?.verification ?? v.verification?.stats} onPick={setPicked} />
        <CriticCard critiques={p?.critiques ?? v.critiques ?? []} />
      </div>

      <div className="stack">
        <DecisionCard v={{ ...v, ...(p ? { decision: p.decision, fit_report: p.fit_report, risk_report: p.risk_report } : {}) }} onPick={setPicked} />
        {quote && (
          <Card title="Price">
            <div className="row gap-m">
              <Stat label="Bid" value={money(quote.amount, quote.bid_type)} />
              {quote.market_band && <Stat label="Market p25–p75" value={`${money(quote.market_band[0])}–${money(quote.market_band[1])}`} />}
            </div>
            <ul className="plain small">{quote.rationale?.map((r: string) => <li key={r}>{r}</li>)}</ul>
          </Card>
        )}
        {preview && (
          <Card title="Dry run: what would be sent">
            <dl className="kv">
              <dt>Connects</dt><dd>{preview.connects_cost} (balance after {preview.connects_balance_after})</dd>
              <dt>Job still open</dt><dd>{preview.checks?.job_open ? "yes" : <span className="bad-text">no</span>}</dd>
              <dt>Can afford</dt><dd>{preview.checks?.can_afford ? "yes" : <span className="bad-text">no</span>}</dd>
              <dt>Policy</dt><dd>{preview.checks?.policy_violations?.length ? <span className="bad-text">{preview.checks.policy_violations.join("; ")}</span> : `no violations (policy ${preview.checks?.policy_version})`}</dd>
              {!!preview.checks?.problems?.length && <><dt>Problems</dt><dd className="bad-text">{preview.checks.problems.join("; ")}</dd></>}
            </dl>
          </Card>
        )}
        {(p?.compliance ?? v.compliance) && <ComplianceCard c={p?.compliance ?? v.compliance} />}
        {v.submission && (
          <Card title="Submitted">
            <p>{v.status_reason}</p>
            {v.submission.result?.proposal_id && <p className="small muted">Proposal {v.submission.result.proposal_id}</p>}
          </Card>
        )}
        {!!v.approvals?.length && (
          <Card title="Your decisions">
            <ul className="plain small">
              {v.approvals.map((a: any, i: number) => (
                <li key={i}>
                  <Badge status={a.action === "approve" ? "submitted" : a.action === "reject" ? "rejected" : "partial"}>{a.action}</Badge>{" "}
                  {a.reason ?? (a.edited_bid ? `bid → $${a.edited_bid}` : "")}
                  {a.learned && Object.keys(a.learned).length > 0 && <span className="muted"> · learned: {JSON.stringify(a.learned)}</span>}
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}

/** The letter with each verified claim highlighted and every [ev_…] marker turned into a clickable chip. */
function Letter({ text, claims, onPick }: { text: string; claims: any[]; onPick: (id: string) => void }) {
  const spans = useMemo(() => {
    const found: { start: number; end: number; label: string }[] = [];
    for (const c of claims) {
      const i = text.indexOf(c.sentence);
      if (i >= 0 && !found.some((f) => i < f.end && i + c.sentence.length > f.start))
        found.push({ start: i, end: i + c.sentence.length, label: c.label });
    }
    found.sort((a, b) => a.start - b.start);
    const out: { s: string; label?: string }[] = [];
    let at = 0;
    for (const f of found) {
      if (f.start > at) out.push({ s: text.slice(at, f.start) });
      out.push({ s: text.slice(f.start, f.end), label: f.label });
      at = f.end;
    }
    if (at < text.length) out.push({ s: text.slice(at) });
    return out;
  }, [text, claims]);

  return (
    <div className="letter">
      {spans.map((sp, i) => (
        <span key={i} className={sp.label ? `hl ${sp.label}` : ""}>{withChips(sp.s, onPick)}</span>
      ))}
    </div>
  );
}

function withChips(s: string, onPick: (id: string) => void): ReactNode[] {
  const out: ReactNode[] = [];
  let at = 0;
  for (const m of s.matchAll(CITE)) {
    out.push(s.slice(at, m.index));
    for (const id of m[1].split(/\s*,\s*/))
      out.push(<button key={`${m.index}-${id}`} className="chip" onClick={() => onPick(id)} title="Show evidence">{id}</button>);
    at = m.index! + m[0].length;
  }
  out.push(s.slice(at));
  return out;
}

function EvidencePanel({ id, d, onClose }: { id: string; d: Detail; onClose: () => void }) {
  const e = d.evidence[id];
  return (
    <Card title={<>Evidence <code>{id}</code></>} actions={<button className="link" onClick={onClose}>Close</button>} className="evidence">
      {e ? (
        <>
          <p><strong>{e.title}</strong> <span className="muted small">({e.type})</span></p>
          <p className="prewrap small">{e.text?.slice(0, 1500)}</p>
        </>
      ) : (
        <p className="muted">This item was not among the evidence the Fit Matcher passed to the writer.</p>
      )}
    </Card>
  );
}

function ClaimsCard({ claims, stats, onPick }: { claims: any[]; stats: any; onPick: (id: string) => void }) {
  return (
    <Card title="Claim check" actions={stats && <span className="muted small">{stats.supported ?? 0} supported · {stats.partly_supported ?? 0} partly · {stats.unsupported ?? 0} unsupported (before removal)</span>}>
      {!claims.length && <p className="muted">No factual claims in this draft.</p>}
      <ul className="claims">
        {claims.map((c, i) => (
          <li key={i}>
            <Badge status={c.label} />
            <span>{c.sentence.replace(CITE, "")}</span>
            <span className="row gap-s wrap">
              {(c.evidence ?? c.evidence_ids.map((x: string) => ({ evidence_id: x }))).map((e: any) => (
                <button key={e.evidence_id} className="chip" onClick={() => onPick(e.evidence_id)} title={e.title}>
                  {e.title ? `${e.title.slice(0, 40)}${e.title.length > 40 ? "…" : ""}` : e.evidence_id}
                </button>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function CriticCard({ critiques }: { critiques: any[] }) {
  if (!critiques.length) return null;
  return (
    <Card title={`Critic (${critiques.length} round${critiques.length > 1 ? "s" : ""})`}>
      {critiques.map((c, i) => (
        <div key={i} className="critique">
          <div className="row gap-s">
            <Badge status={c.verdict} /> <span className="muted small">round {i + 1}</span>
            {c.scores && <span className="muted small">{Object.entries(c.scores).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>}
          </div>
          {!!c.issues?.length && <ul className="small">{c.issues.map((x: string) => <li key={x}>{x}</li>)}</ul>}
        </div>
      ))}
    </Card>
  );
}

function ComplianceCard({ c }: { c: any }) {
  return (
    <Card title="Compliance Guard">
      <p>{c.allowed ? <Badge status="pass">allowed</Badge> : <Badge status="blocked">blocked</Badge>} {c.reason}</p>
      {!!c.violations?.length && <ul className="small bad-text">{c.violations.map((x: string) => <li key={x}>{x}</li>)}</ul>}
      {!!c.redactions?.length && <p className="small muted">Removed: {c.redactions.join("; ")}</p>}
    </Card>
  );
}

function DecisionCard({ v, onPick }: { v: Record<string, any>; onPick?: (id: string) => void }) {
  const dec = v.decision, fit = v.fit_report, risk = v.risk_report;
  if (!dec && !fit && !risk) return null;
  return (
    <>
      {dec && (
        <Card title="Recommendation">
          <p><Badge status={dec.action === "bid" ? "submitted" : dec.action === "watch" ? "watching" : "skipped"}>{dec.action}</Badge> {dec.auto && <span className="muted small">(automatic under your autonomy setting)</span>}</p>
          <ul className="plain small">{dec.reasons?.map((r: string) => <li key={r}>{r}</li>)}</ul>
        </Card>
      )}
      {risk && (
        <Card title="Risk">
          <div className="row gap-m"><Meter value={risk.score} tone={risk.score >= 0.7 ? "bad" : risk.score >= 0.4 ? "warn" : "good"} /></div>
          {!!risk.flags?.length && <div className="tags">{risk.flags.map((f: string) => <em key={f} className="flag">{f.replace(/_/g, " ")}</em>)}</div>}
          <p className="small">{risk.explanation}</p>
          {risk.injection_detected && (
            <div className="note bad small">
              The job post contains text that tries to instruct the AI. It was ignored{risk.injection_excerpt ? `: “${risk.injection_excerpt}”` : "."}
            </div>
          )}
        </Card>
      )}
      {fit && (
        <Card title="Fit">
          <div className="row gap-m"><Meter value={fit.score} tone={fit.score >= 0.3 ? "good" : "warn"} /> <span className="small muted">{fit.reasons?.join("; ")}</span></div>
          <table className="matrix">
            <tbody>
              {fit.matrix?.map((m: any, i: number) => (
                <tr key={i}>
                  <td><Badge status={m.status} /></td>
                  <td>
                    {m.requirement}
                    {m.evidence?.[0] && (
                      <div className="small muted">
                        best: {onPick ? <button className="link" onClick={() => onPick(m.evidence[0].evidence_id)}>{m.evidence[0].title}</button> : m.evidence[0].title}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!!fit.missing_skills?.length && <p className="small">Not in your portfolio: {fit.missing_skills.join(", ")}</p>}
        </Card>
      )}
    </>
  );
}

// ------------------------------------------------------------------ reply studio (conversation graph)
function ReplyStudio({ d, running, act }: { d: Detail; running: boolean; act: (p: string, b?: unknown) => Promise<void> }) {
  const p = d.pending_approval?.kind === "message" ? d.pending_approval : null;
  const v = d.values;
  const reply = p?.reply ?? v.reply;
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async (body: any) => {
    setBusy(true);
    try {
      await act("decision", body);
      setMode("view");
    } catch {
      /* shown above */
    } finally {
      setBusy(false);
    }
  };
  const msgs: any[] = v.messages ?? [];
  return (
    <div className="studio">
      <div className="stack">
        <Card title="Conversation">
          <div className="thread">
            {msgs.map((m) => (
              <div key={m.message_id} className={`msg ${m.sender}`}>
                <div className="small muted">{m.sender === "client" ? "Client" : "You"} · {m.sent_at?.slice(0, 16).replace("T", " ")}</div>
                <div className="prewrap">{m.text}</div>
              </div>
            ))}
          </div>
        </Card>
        {!reply && running && <Empty>Drafting a reply…</Empty>}
        {reply && (
          <Card title="Drafted reply" actions={(p?.intent ?? v.intent) && <span className="muted small">client intent: {(p?.intent ?? v.intent).intent?.replace(/_/g, " ")}</span>}>
            {p && !running && mode === "view" && (
              <div className="approval-bar">
                <div><strong>Review before it is sent.</strong></div>
                <div className="row gap-s">
                  <button className="primary" disabled={busy} onClick={() => send({ action: "approve" })}>Approve and send</button>
                  <button disabled={busy} onClick={() => { setText(reply.text); setMode("edit"); }}>Edit</button>
                  <button className="danger" disabled={busy} onClick={() => setMode("reject")}>Reject</button>
                </div>
              </div>
            )}
            {mode === "edit" ? (
              <div className="stack">
                <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} />
                <p className="hint">Your edit is checked by the Compliance Guard again before sending.</p>
                <div className="row gap-s">
                  <button className="primary" disabled={busy || !text.trim()} onClick={() => send({ action: "edit", edited_text: text })}>Send edited reply</button>
                  <button onClick={() => setMode("view")}>Cancel</button>
                </div>
              </div>
            ) : mode === "reject" ? (
              <div className="stack">
                <textarea rows={3} placeholder="Why? (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
                <div className="row gap-s">
                  <button className="danger" disabled={!reason.trim() || busy} onClick={() => send({ action: "reject", reason: reason.trim() })}>Reject</button>
                  <button onClick={() => setMode("view")}>Cancel</button>
                </div>
              </div>
            ) : (
              <p className="prewrap letter">{reply.text}</p>
            )}
          </Card>
        )}
      </div>
      <div className="stack">
        {!!(p?.slots ?? v.slots)?.length && (
          <Card title="Free interview slots">
            <ul className="plain small">{(p?.slots ?? v.slots).map((s: any) => <li key={s.start}>{s.start?.replace("T", " ").slice(0, 16)} {s.label ?? ""}</li>)}</ul>
            <p className="hint">Sending holds the first slot as tentative in your calendar.</p>
          </Card>
        )}
        {(p?.compliance ?? v.compliance) && <ComplianceCard c={p?.compliance ?? v.compliance} />}
        {v.sent && <Card title="Sent"><p>{v.status_reason}</p>{v.hold?.result && <p className="small muted">Tentative calendar hold created.</p>}</Card>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ run viewer
function RunViewer({ steps, events, d, running, cost }: { steps: [string, string][]; events: RunEvent[]; d: Detail; running: boolean; cost: number }) {
  const seen = new Map<string, RunEvent[]>();
  for (const e of events) if (e.type === "node" && e.node) seen.set(e.node, [...(seen.get(e.node) ?? []), e]);
  const last = [...events].reverse().find((e) => e.type === "node");
  const interrupted = events.some((e) => e.type === "interrupt");
  const t0 = events[0]?.ts;
  const usage = d.values.usage ?? {};
  return (
    <div className="studio">
      <Card title="Steps">
        <ol className="timeline">
          {steps.map(([key, label]) => {
            const hits = seen.get(key) ?? [];
            const approvalStep = key === "approval_gate" || key === "approval";
            const state = hits.length ? "done" : approvalStep && interrupted ? "wait" : "todo";
            return (
              <li key={key} className={state}>
                <span className="tl-dot" />
                <div>
                  <strong>{label}</strong>
                  {hits.length > 1 && <span className="muted small"> ×{hits.length}</span>}
                  {approvalStep && interrupted && !hits.length && <span className="small"> — waiting for you</span>}
                  {hits.map((h, i) => (h.reason || h.status) && (
                    <div key={i} className="small muted">{h.status && h.status !== "running" ? <Badge status={h.status} /> : null} {h.reason}</div>
                  ))}
                </div>
              </li>
            );
          })}
        </ol>
        {running && last && <Spinner label={`working (last: ${last.node})`} />}
      </Card>
      <div className="stack">
        <Card title="Cost and budget">
          <div className="row gap-m wrap">
            <Stat label="Cost" value={`$${cost.toFixed(4)}`} />
            <Stat label="Tokens" value={(d.totals.tokens_in + d.totals.tokens_out).toLocaleString()} />
            <Stat label="Calls" value={d.totals.events} />
            <Stat label="Graph steps" value={d.values.steps ?? 0} />
          </div>
          {Object.keys(usage).length > 0 && (
            <table className="mini">
              <thead><tr><th>Node</th><th>Calls</th><th>Tokens</th><th>Cost</th></tr></thead>
              <tbody>
                {Object.entries(usage).filter(([, u]: any) => typeof u === "object")
                  .sort(([a], [b]) => (a === "total" ? 1 : 0) - (b === "total" ? 1 : 0)).map(([k, u]: any) => (
                  <tr key={k} className={k === "total" ? "total" : ""}><td>{k}</td><td>{u.calls ?? ""}</td><td>{((u.tokens_in ?? 0) + (u.tokens_out ?? 0)).toLocaleString()}</td><td>{u.cost_usd != null ? `$${u.cost_usd.toFixed(4)}` : ""}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Event log">
          <ul className="log small">
            {events.map((e) => (
              <li key={e.seq}>
                <span className="muted">+{t0 ? (e.ts - t0).toFixed(1) : "0"}s</span> <code>{e.type}</code> {e.node ?? ""} {e.reason ?? e.message ?? (e.type === "done" ? e.status : "")}
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Checkpoints">
          <p className="hint">State is saved after every step, so a pending approval survives a refresh or a server restart.</p>
          <ul className="log small">
            {d.history.slice(0, 30).map((h) => (
              <li key={h.checkpoint_id}>step {h.step} · next: {h.next.join(", ") || "end"} · {h.status ?? ""}</li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

function Versions({ d }: { d: Detail }) {
  const drafts: any[] = d.values.drafts ?? [];
  const critiques: any[] = d.values.critiques ?? [];
  if (!drafts.length) return <Empty>No drafts yet.</Empty>;
  return (
    <div className="stack">
      {drafts.map((x, i) => {
        const crit = x.stage === "written" ? critiques.find((c) => c.revision === (x.revision ?? 0) - 1) : null;
        return (
          <Card key={i} title={<>{i + 1}. {x.stage?.replace(/_/g, " ")} <span className="muted small">revision {x.revision ?? 0}</span></>}>
            {crit && <p className="small muted">Rewritten after the Critic said: {crit.issues?.slice(0, 2).join(" ")}</p>}
            <p className="prewrap small">{x.cover_letter}</p>
          </Card>
        );
      })}
    </div>
  );
}

function Trace({ d }: { d: Detail }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!d.trace.length) return <Empty>No LLM or tool calls recorded yet.</Empty>;
  return (
    <Card title="Every LLM and tool call (inputs masked for personal data)">
      <div className="table-wrap">
        <table className="trace">
          <thead><tr><th>Node</th><th>Kind</th><th>Name</th><th>ms</th><th>Tokens</th><th>Cost</th><th>OK</th></tr></thead>
          <tbody>
            {d.trace.map((t) => (
              <Fragment key={t.seq}>
                <tr className="clickable" onClick={() => setOpen(open === t.seq ? null : t.seq)}>
                  <td>{t.node}</td><td>{t.kind}</td><td>{t.name}</td><td>{Math.round(t.ms)}</td>
                  <td>{t.tokens_in + t.tokens_out || ""}</td><td>{t.cost_usd ? `$${t.cost_usd.toFixed(5)}` : ""}</td>
                  <td>{t.ok ? "✓" : <span className="bad-text">✗</span>}</td>
                </tr>
                {open === t.seq && (
                  <tr><td colSpan={7}><pre className="io">{"input: " + (t.input ?? "") + "\n\noutput: " + (t.output ?? "")}</pre></td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
