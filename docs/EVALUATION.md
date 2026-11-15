# Evaluation

How each number in `evals/results/RESULTS.md` was produced, and where it deviates from the brief.
The tables are built by `python -m evals.runners.report` from the per-job rows in
`evals/results/runs/*.jsonl`. Re-running a runner skips the jobs that are already done.

## Budget: why the samples are small

Everything runs on Groq's free tier: 8,000 tokens per minute and 200,000 tokens per day, per
model. Paid upgrades were closed while this was built. The daily limit behaves like a bucket that
refills steadily, about 8,300 tokens an hour. That is an observation, not documentation: the
waits the API asks for fit that rate, and more than 200k tokens went through one model between
10:00 and 21:00. A full bid run uses
10–20k tokens, so the brief's sample sizes (50 jobs × 3 systems for question C, and so on) would
need several million tokens. Each study therefore runs on its own model, so the daily limits add up,
and on the largest sample that fits:

| Study | Model | Jobs / items | Brief asks for |
|---|---|---|---|
| Decisions (bid-or-skip F1, scams, price band) | gpt-oss-20b (shipped analysis models) | 36 dev jobs | dev set |
| B: V0 vs shipped retriever | qwen3.8-27b as the writer (same for both arms) | 11 bid jobs × 2 arms | — |
| C: ReAct vs supervisor vs graph | gpt-oss-20b for every system | 9 jobs | 50 |
| A: tool granularity | gpt-oss-120b | 40 tasks × 3 tool sets | 40 |
| Injection attack success | qwen3.8-27b writer, Prompt Guard detector | 15 jobs | planted-injection jobs |
| Judge calibration | gpt-oss-120b judge | 30 items labelled by hand | 30 |
| X2 pricing A/B | none (GigSim) | all drafts, paired | — |

Three models could not be used:
- `gpt-oss-safeguard-20b` has a free limit of 2,000 tokens per minute, smaller than one request.
- `qwen3.8-27b` corrupts tool names in multi-tool calls on Groq (`get_client buzz`, `get_e Dining`),
  so it is only used where no tools are called (writing, judging).
- A local model was not an option: the machine has no GPU and 5.8 GB of RAM.

Every run disables the fallback model (`GIGBID_NO_FALLBACK=1`), so a rate limit can never quietly
change which model is being measured. Instead, a run waits out rate limits (`GIGBID_RATE_WAIT`).

- **Latency excludes the waits.** Every pause is recorded (`agent/llm.py: rate_waited`), and rows
  carry `seconds_net`. Decision rows from before pauses were recorded are marked `rate_limited`
  when the run log shows a daily-limit pause, and they are left out of latency.
- **The LangChain baselines wait at the HTTP layer** (`baselines.py: WaitOn429`), so a long pause
  doesn't restart an agent run and spend its tokens twice.
- **Studies that may not finish run interleaved:** question C job by job across the three systems,
  and question A task by task across the three tool sets. Wherever the budget stops them, the
  comparison is paired (`report.py` scores only jobs every system has finished).

## Shared setup

- **Jobs** are drawn once with seed 4016 from the labelled dev sets and saved to
  `evals/datasets/eval_sets.json`. Per persona, the decision set holds 5 bids, 1 injected bid,
  2 no-fit skips, 2 budget skips and 2 scams.
- **Users** are the persona profiles as onboarding stores them (`evals/runners/common.py: user_for`).
- **Each system under test gets its own GigSim** (own port and state, chaos off), so proposals never
  interact across systems. Outcomes come from submitting each draft and moving the simulated clock
  14 days forward. The evaluation harness plays the approving human; that only ever happens on
  these private simulators.
- **Unsupported claims are counted with one yardstick for every system.** Each sentence that claims
  past work is checked by the shipped verifier against the 5 portfolio documents retrieved for that
  sentence (`evals/runners/report.py: unsupported`). A system is not graded only against the evidence
  it chose to retrieve.

## Decisions

`graph_runs --set decisions --stop writer` runs the shipped graph (load job, the three parallel
analyses, decision, pricing) and stops before the writer.

- Bid-or-skip: "bid" vs anything else, against the dev label.
- Scam detection: the decision's risk score above `maximum_risk_score`, or a scam rule flag.
- Price in band: the quote falls inside the dev set's fair price band.

One job first failed with a provider error ("Failed to generate JSON", HTTP 400). That error
bypassed the retry and fallback logic (fixed in `agent/llm.py`, with a regression test). The job
was re-run with the fix.

## R&D question B

`proposal_variants` takes each job's state saved just before the writer by the decisions run, so
both arms share the same job spec, risk report and price. It then runs only what depends on
retrieval: Fit Matcher → Writer → Verifier, with `v0_naive` or the shipped `combined`. Retrieval
quality (Recall@5, nDCG@10) is measured over all 285 dev bid jobs:
- V0: `rag_retrieval_variants.json`;
- combined: the CI gate's retrieval check, `ci_gate.json`;
both use the same function. The ablation and per-persona and per-job-type breakdowns are in
`rag_retrieval_ablation.md` and `rag/variants/README.md` (step 3).

## R&D question C

All three systems use gpt-oss-20b, the same MCP tools (read-only; nothing can send), the policy and
the user's settings, and must return the same JSON.

| System | How it is built |
|---|---|
| (a) ReAct agent | `create_react_agent`, all 7 read tools |
| (b) Supervisor | `langgraph-supervisor` with a researcher, an evidence finder and a writer |
| (c) Our graph | `open_20b` profile, starting from the decisions run's analysis |

Both baselines get standard context management: history trimmed before each model call, evidence
searches capped at 3 results, tool output truncated. Without it they fail on the first job, because
the free tier rejects any single request over about 7k tokens.

## R&D question A

40 requests, each with one correct call (`evals/datasets/tool_tasks.json`), several of them
deliberate near-misses. The model must call exactly one tool. Low reasoning effort and temperature 0
are used for every set.

| Set | Tools |
|---|---|
| fine | the 21 MCP tools as shipped |
| coarse | one tool per server, with an `action` argument |
| best | the 21 tools with rewritten "use this when / not for" descriptions |

## Injection attack success rate

The study uses the 15 dev jobs whose planted instruction leaves a checkable string in a proposal:
GigSim's own canaries, such as "continue on WhatsApp" or "worked for Google". Attacks are added one
defence layer at a time; see `evals/runners/injection_eval.py`. Injections aimed at scoring or at the
Compliance Guard leave no text trace, so only their detection is reported (decisions run, risk flags).

### Regex detector v2 and the targeted re-run

The decisions run showed the injection regex flagging requirements that describe a bot ("The AI
Scanbot should…"). `python -m evals.runners.injection_regex` measures both pattern sets on every
labelled dev job (`evals/results/injection_regex.json`): v1 had precision 0.80, v2 (addressed
instructions only) has 1.00, and recall is 0.89 for both. v2 was written by looking at these same
jobs, so it is not a held-out result.

Risk prompt v2 stops an injection, on its own, from raising the risk score. Both changes can only
affect jobs where either regex version fires, the injected bids and the scams: 11 of the 36. Those
were re-run as `decisions_v2` (`graph_runs --only`), and the report compares them with v1 (every job flagged for injection in the first run is among them).
Result (`summary.json`, `decisions_v2`): 8 → 10 of 11 correct, 0 of 6 scams bid on; F1 on all 36
jobs 0.882 → 0.944. The one injected bid still skipped asks for login credentials, a hard skip rule
kept on purpose.

## Judge calibration

The 30 items are the B drafts plus injection-study drafts, shuffled. The labeller does not see which
system wrote which draft. The judge prompt (`agent/prompts/judge.md`) and the labelling tool use the
same rubric. The judge (gpt-oss-120b) is a different model family from the writer (Qwen). Agreement is
reported as Cohen's kappa:
- quadratic-weighted, on the 1–5 score;
- unweighted, on "send as is".

## Limits

- **Small samples.** Differences of one or two jobs are noise. The report gives counts, not just
  rates.
- **Partly circular labels.** The dev labels for fit and scams come from the same data pipeline that
  built GigSim. Fit labels are skill overlap, which the calibrated fit thresholds were tuned against
  (on a different half of the dev set). The scam posts are synthetic, so rules written for them score
  well.
- **Simulated outcomes.** GigSim's reply model rewards specificity and fair pricing. Reply rates
  measure fit to that model, and with about 1 reply in 8 proposals, a handful of proposals says
  little.
