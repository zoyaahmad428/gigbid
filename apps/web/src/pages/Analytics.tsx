import { useApi } from "../hooks";
import { Bars, Card, ErrorNote, Spinner, Stat } from "../ui";

const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

export default function Analytics() {
  const { data: a, error } = useApi<any>("/analytics");
  if (error) return <ErrorNote error={error} />;
  if (!a) return <Spinner />;
  const funnel: [string, number][] = [
    ["analysed", a.runs.bid],
    ["drafted for you", (a.runs.by_status.awaiting_approval ?? 0) + a.approval.submitted + a.approval.rejected],
    ["sent", a.proposals.sent],
    ["client engaged", (a.proposals.by_status.replied ?? 0) + (a.proposals.by_status.interview ?? 0) + (a.proposals.by_status.hired ?? 0)],
    ["hired", a.proposals.hired],
  ];
  const fitHist: [string, number][] = Array.from({ length: 10 }, (_, i) => [`${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}`, a.fit.histogram[i] ?? 0]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Analytics</h1>
          <p className="muted">Measured from your own runs and the marketplace. Nothing here is estimated.</p>
        </div>
      </div>
      <div className="stats">
        <Stat label="Jobs analysed" value={a.runs.bid} />
        <Stat label="Approval rate" value={pct(a.approval.approval_rate)} hint={`${a.approval.submitted} sent · ${a.approval.rejected} rejected`} />
        <Stat label="Edited before sending" value={a.approval.edited_before_approval} />
        <Stat label="Client response rate" value={pct(a.proposals.response_rate)} hint={`of ${a.proposals.sent} sent`} />
        <Stat label="Connects spent" value={a.connects.spent} hint={`balance ${a.connects.balance ?? "—"} / ${a.connects.weekly_allowance ?? "—"} a week`} />
        <Stat label="LLM cost" value={`$${a.cost.total_usd.toFixed(3)}`} hint={a.cost.per_run_usd != null ? `$${a.cost.per_run_usd.toFixed(4)} per run` : undefined} />
        <Stat label="Tokens per job" value={a.cost.tokens_per_bid_run?.toLocaleString()} />
        <Stat label="Mean fit" value={a.fit.mean?.toFixed(2)} />
      </div>
      <div className="two-col">
        <Card title="Funnel"><Bars rows={funnel} /></Card>
        <Card title="Runs by status"><Bars rows={Object.entries(a.runs.by_status) as [string, number][]} /></Card>
        <Card title="Why jobs were skipped or watched"><Bars rows={a.skip_reasons} /></Card>
        <Card title="Fit score distribution"><Bars rows={fitHist} /></Card>
        <Card title="Proposals on the marketplace"><Bars rows={Object.entries(a.proposals.by_status) as [string, number][]} /></Card>
        <Card title="Jobs analysed per day"><Bars rows={a.runs_per_day} /></Card>
      </div>
    </div>
  );
}
