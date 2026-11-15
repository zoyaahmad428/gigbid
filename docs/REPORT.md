---
title: "GigBid: R&D report"
subtitle: "AI-4016 assignment II, engineering design review"
date: "15 November 2026"
---

Every number in this report comes from a file in `evals/results/`, named next to it. Per-job
rows are in `evals/results/runs/`, and `python -m evals.runners.report` rebuilds the tables
(`evals/results/RESULTS.md`). How each number was produced is in `docs/EVALUATION.md`.

**One constraint shapes every result.** All model calls run on Groq's free tier: about
200,000 tokens per model per rolling 24 hours, 8,000 tokens per minute, and no single request
over about 7,000 input tokens. Paid upgrades were closed while this was built. Samples are
therefore smaller than the brief asks for, and they are reported as counts, not just rates.
Where a difference is within noise, the report says so.

# 1. Problem and constraints

A freelancer on Upwork spends hours reading job posts and pays Connects for every proposal, so a
weak bid costs money. Some posts are scams that ask for free work or payment off the platform.
GigBid reads the feed, decides which jobs are worth a bid, drafts a proposal from the
freelancer's own evidence, and stops. **The freelancer approves every proposal, message and
Connects spend.**

**Never automated** (enforced in code, not in prompts; section 9):

- sending a proposal or message;
- spending Connects;
- accepting an interview;
- following instructions written inside a job post or client message;
- claiming experience the portfolio doesn't support.

**Declared budgets** (`agent/config.yaml: budgets.declared`, open profile, excluding provider
rate-limit pauses):

| Unit | p95 latency | Cost |
|---|---|---|
| Job analysed (load, three analyses, decision, price) | 45 s | $0.003 |
| Proposal (job analysed + writer, verifier, critic, compliance) | 120 s | $0.01 |

Hard limits per thread are 30 graph steps and 40,000 tokens; above either, the thread moves to
`needs_attention` (`agent/nodes/base.py`).

# 2. Architecture

![Bid graph, rendered from code (docs/images/bid_graph.mmd)](images/bid_graph.png){width=62%}

**Two graphs** (`agent/graphs/`):

- **Bid graph, one thread per job.** `load_job` fans out with `Send` to three parallel analyses:
  - the Analyst turns the post into a `JobSpec`;
  - the Risk Assessor produces a `RiskReport` with scam and injection flags;
  - the Fit Matcher builds a `FitReport`: a requirement-to-evidence matrix from the Portfolio server.

  `decision` then chooses bid, watch or skip. For a bid, the graph continues:
  - Pricing (rules, no model);
  - Writer;
  - Verifier, which removes unsupported claims;
  - Critic, which can send the draft back at most twice;
  - Compliance Guard;
  - `approval_gate`, a LangGraph `interrupt`. The checkpoint survives a restart, and the
    approval payload carries the draft, the evidence behind every claim, the risk report, the
    price rationale and the marketplace's dry-run preview.
- **Conversation graph, one thread per client message:** read → intent → (interview slots) →
  draft reply → compliance → approval → send.

**Why each agent exists:**

- The three analyses are independent, so they run in parallel; risk must never wait on writing.
- The Verifier is separate from the Writer, so a claim is checked by code, not by the model that
  wrote it.
- The Critic is bounded, because unbounded critique loops were seen to push the Writer towards
  invented figures (`docs/AI_USAGE.md`).
- Compliance runs last, on the final text.

Hand-offs are Pydantic objects (`agent/handoffs.py`).

**Marketplace port** (`agent/marketplace_port.py`): GigSim and live Upwork adapters implement
the same interface, so the graph never knows which marketplace it is using. The live adapter is
a stub that fails loudly (`mcp_servers/marketplace/adapters/upwork_live.py`); A4 is not built.

**MCP surface:** three servers we wrote, with 21 tools, annotations, bearer auth, idempotent
writes, rate limits and an append-only audit log (`docs/TOOLS.md`):

- **Portfolio**, read-only evidence search;
- **Marketplace**, which wraps the port, with dry-run by default on every write;
- **Workspace**, calendar holds and email drafts.

Each agent sees an allowlist of tools (`agent/mcp_client.py`); the Writer has no write tools.

**Stores kept apart:**

- graph state, checkpointed (SQLite locally, Postgres deployed);
- execution traces: every tool and model call, PII-masked, with tokens and cost (`agent/trace.py`);
- long-term memory: only promoted style rules and outcome lessons (`agent/memory/`).

Raw tool output never enters memory.

# 3. R&D question A: tool granularity

**Hypothesis.** Fewer, coarser tools cost fewer tokens per call but select arguments less
reliably. Fine-grained tools with "use this when / not for" descriptions select best.

**Setup** (`evals/runners/tool_study.py`):

- 40 requests with one correct call each (`evals/datasets/tool_tasks.json`) in 9 categories,
  several of them deliberate near-misses (`get_job` vs `search_jobs`, `list_messages` vs
  `get_events`).
- Three tool sets:
  - **fine**: the 21 MCP tools as shipped;
  - **coarse**: one tool per server, with an `action` argument;
  - **best**: the 21 tools with rewritten descriptions.
- Model: gpt-oss-120b, temperature 0, low reasoning effort, `tool_choice=required`.
- Tasks run one at a time across all three sets, so a run cut short by the budget is still
  paired.

**Results.** The run stopped at its 50k-token cap after 31 of the 40 tasks, all 9 categories
covered, the same 31 for every set (`evals/results/tool_granularity.json`):

| Tool set | Tasks | Right tool | Arguments right | Fully correct call | Tokens per task | Cost per task |
|---|---|---|---|---|---|---|
| fine (shipped) | 31 | 1.0 | 0.8333 | 0.9355 | 2,115 | $0.000342 |
| coarse | 31 | 0.8387 | 0.619 | 0.7097 | 588 | $0.000118 |
| best | 31 | 1.0 | 0.8333 | 0.9355 | 2,209 | $0.000355 |

- **The hypothesis held for coarse tools.** They cost 72% fewer tokens but got 5 of 31 tool
  choices wrong. Three called a fine-grained name (`get_free_slots`) that doesn't exist in the
  coarse set, so the provider rejected them. One sent a write to a read: `submit_proposal` →
  `list_proposals`.
- **Rewriting the descriptions changed nothing.** Fine and best agreed on every one of the 31
  tasks (`runs/tool_fine.jsonl`, `runs/tool_best.jsonl`). At 120b the shipped descriptions are
  already enough, and the rewrite only adds about 100 tokens.
- **Writes are the weak spot for every set.** On the write tasks, fine and best both got 2 of 4
  fully right; they picked the right tool but missed an argument.

**Shipped choice: fine.** The saving from coarse tools is $0.00022 per call. One wrong write
costs far more, and the approval gate would only catch it after the fact. The writes result says
argument schemas, not tool count, are the next thing to fix.

A note on model choice: qwen3.8-27b was tried first and dropped. On Groq it emits tool names
that don't exist (asked for `get_job`, it called `search_e evidence`). The provider then
rejects the call, which would measure the provider, not the tool design.

# 4. R&D question B: retrieval, claim verification and the shipped choice

## Retrieval variants

Measured over all 285 dev bid jobs (100 AI/ML, 100 full-stack, 85 data analyst). Gold labels are
the documents of the best-matching portfolio projects. Embeddings are bge-small; the reranker is
jina-turbo, on CPU. Each row is the all-persona row of its results file:

- V0, V2, V3, V5 and V8: `evals/results/rag_retrieval_variants.md`;
- V1, V4 and combined + rerank: `rag_retrieval_fixed.md`, re-run after two bugs were fixed;
- the shipped combination: `rag_retrieval_fixed_ablation.md`.

| Variant | Recall@5 | MRR | nDCG@10 | p95 latency (s) |
|---|---|---|---|---|
| V0 dense, fixed chunks, whole post as query | 0.366 | 0.467 | 0.463 | 0.16 |
| V1 query decomposition | 0.370 | 0.487 | 0.463 | 0.11 |
| V2 hybrid BM25 + dense | 0.405 | 0.513 | 0.505 | 0.33 |
| V3 cross-encoder rerank | 0.384 | 0.503 | 0.480 | 2.54 |
| V4 outcome-weighted transfer | 0.509 | 0.592 | 0.585 | 0.19 |
| V5 multi-vector projects | 0.401 | 0.411 | 0.499 | 0.17 |
| V8 corrective (grade, re-query, refuse) | 0.291 | 0.372 | 0.354 | 2.77 |
| **combined V1+V2+V4+V5 (shipped)** | **0.588** | **0.642** | **0.670** | **0.16** |
| combined + V3 rerank | 0.595 | 0.651 | 0.677 | 5.85 |

**Ablation** (`evals/results/rag_retrieval_fixed_ablation.md`): remove one component from the
shipped combination. Recall@5 per persona:

| | AI/ML | Full-stack | Data | All |
|---|---|---|---|---|
| combined | 0.604 | 0.577 | 0.584 | 0.588 |
| − decomposition | 0.566 | 0.522 | 0.475 | 0.523 |
| − BM25 | 0.550 | 0.459 | 0.588 | 0.529 |
| − dense | 0.678 | 0.511 | 0.579 | 0.590 |
| − outcome transfer | 0.428 | 0.480 | 0.468 | 0.458 |
| − multi-vector | 0.586 | 0.533 | 0.572 | 0.563 |

**Findings:**

- **Outcome transfer (V4) is the strongest component.** Removing it costs 0.13 Recall@5 overall,
  and 0.18 for AI/ML.
- **Decomposition is neutral alone but matters in the combination,** worth 0.07 overall.
- **Dense retrieval is a wash overall.** Without it, Recall@5 is 0.590 instead of 0.588, and AI/ML
  improves to 0.678. But full-stack drops from 0.577 to 0.511: those posts use paraphrase more
  than technology names. It stays for that persona, at no measurable latency cost.
- **Reranking adds 0.007 Recall@5 for about 35× the latency, so it isn't shipped.**
- **V8 refuses too often:** 20–51% of bid jobs that do have evidence (`rag/variants/README.md`).
- **Letting past proposals into the results collapses Recall@5 to under 0.1**
  (`rag_retrieval_pool_all.md`). They outrank the portfolio documents they cite.

## From retrieval to proposals

Both arms start from the same saved state for each job, the decisions run's analysis and price,
and use the same writer (qwen3.8-27b). Only Fit Matcher → Writer → Verifier is re-run, with each
retriever. The sample is 12 bid jobs, 4 per persona (`evals/results/summary.json`,
`proposal_quality`; human scores from `evals/results/judge_calibration.json`).

| | V0 | Shipped (combined) |
|---|---|---|
| Recall@5 / nDCG@10 (285 jobs) | 0.366 / 0.463 | 0.588 / 0.670 |
| Claims per proposal, first draft | 2.50 | 1.75 |
| Unsupported claims per proposal, first draft | 0.33 | 0.17 |
| Unsupported per proposal after verification (independent yardstick) | 0.17 | 0.00 |
| LLM judge v2 score (1–5) | 3.42 | 3.42 |
| Human score (1–5), 11 drafts per arm; sign test on 11 pairs, p = 0.625 | 2.73 | 2.91 (better on 3 pairs, worse on 1) |
| GigSim reply rate (14 simulated days) | 2/12 | 1/12 (and 1 interview) |
| p95 latency, retrieval-dependent stages | 23.6 s | 22.9 s |
| Cost per proposal | $0.00067 | $0.00059 |

The independent yardstick re-checks every sentence that claims past work against the top-5
portfolio documents retrieved for that sentence. It doesn't use the evidence each system chose,
so every arm is graded the same way (`evals/runners/report.py: unsupported`).

**Claim verification** (`evals/results/verify_claims.json`): it detected all 30 swapped-skill
claims in the dev check and 21 of 22 on the held-out split. Unsupported claims are removed or
rewritten before the Critic sees the draft.

**Where better retrieval did not raise the reply rate, and why.** The shipped retriever halved
unsupported claims but got 1 reply against V0's 2. Three reasons, in order of weight:

1. **Noise.** 3 of 12 jobs are discordant: V0 alone got a reply on 2 and the shipped arm alone on
   1. McNemar's exact p = 1.0. Twelve pairs cannot show a difference at a ~12% reply rate.
2. **The simulated client can't tell a true claim from an invented one.** GigSim's reply model
   (`gigsim/client_sim.py`, public code) scores evidence by *counting* number-like strings
   ("40%", "3x", "$5k"), not by checking them. The two arms score alike on this feature
   (0.45 vs 0.42 of the maximum). Verification removes *unsupported* figures, which a real
   client might punish but GigSim rewards.
3. **Price dominates.** The score loses 2 × overprice. In 4 of 12 jobs our price was 4–67% above
   the fair band, the same in both arms, because the freelancer's hourly floor is above the
   band (section 8).

**Shipped choice: combined, without reranking.** It is the best retrieval at 0.16 s and halves
unsupported claims. The reply rate is not evidence against it at this sample size. GigSim
measures surface features, so the claim-level metrics are the ones that tell the arms apart here.

## Judge calibration

I labelled 30 drafts blind: the 22 B drafts plus 8 from the injection study, in random order,
with the same rubric the judge gets (`evals/runners/label.py`). The judge is gpt-oss-120b, a
different family from the Qwen writer (`evals/results/judge_calibration.json`):

| Judge prompt | Kappa, 1–5 score (quadratic) | Kappa, "send as is" | Within one point | Mean score, judge / human | Would send, judge / human | Kappa, 8 items with a planted instruction |
|---|---|---|---|---|---|---|
| v1 (`judge.md`) | 0.29 | 0.011 | 0.567 | 3.833 / 2.867 | 23 / 9 | −0.08 |
| v2 (`judge_v2.md`) | 0.612 | 0.6 | 0.867 | 3.633 / 2.867 | 15 / 9 | 0.343 |

v1 had three failures, and I was right in each of the disagreements behind them:

- **It obeyed the job post.** It marked down letters for *not* starting with "BANANA" or "Let's
  continue on WhatsApp" (i12, i16, i23, i29; i29 was my only 5). The judge had the same
  injection weakness the writer prompt was built to avoid.
- **It credited proof from the wrong field.** It gave a full-stack developer's letter for a
  cold-email copywriting job a 4 and "send" (i20; I gave 1).
- **It didn't notice invented context.** It gave i04, which invents a legacy migration the post
  never mentions, a 4 and "send" (I gave 1). **v2 still gives it a 5.**

v2 treats the post as untrusted, checks fit, grounding and the client's explicit asks in order,
and caps the score for each failure. **v2 was written after I saw these disagreements and is
scored on the same 30 items, so 0.61 is an optimistic estimate**; a fresh labelled set would be
needed to confirm it. It is still about 0.8 points lenient. So the judge column above is
supporting evidence only. On my own labels, the arms differ by one pair out of 11
(`human_by_arm` in the same file), which is no evidence either way.

# 5. R&D question C: single agent vs prebuilt supervisor vs our graph

**Setup** (`evals/runners/baselines.py`, `graph_runs.py --set c`). All three systems:

- use gpt-oss-20b;
- get the same read-only MCP tools, the marketplace policy and the user's settings;
- must return the same JSON (decision, price, letter).

| System | How it is built |
|---|---|
| (a) ReAct | `create_react_agent` with all 7 tools |
| (b) Supervisor | `langgraph-supervisor` with researcher, evidence-finder and writer workers |
| (c) Our graph | the shipped graph under the `open_20b` profile |

Both baselines get standard context management (trimmed history, capped searches, truncated tool
output); without it they exceed the free tier's request size on the first job. Jobs run one at
a time across all three systems, so the comparison stays paired however far the budget reaches.
The plan is 9 jobs: per persona, one clean bid, one bid with a planted injection, and one scam.

**The budget stopped this study after one paired job.** All three systems run on gpt-oss-20b, the
same model the live deployment uses, and the free tier's daily limit for it ran out on the day of
submission. `report.py` scores only jobs that every system finished, so the table below is a
single job (`job_cea72b83bb`, AI/ML persona, a clean bid). It is a smoke test of the comparison,
not evidence for one architecture over another.

| System | Bid-or-skip correct | Scams bid on | Proposals | Unsupported claims / proposal | Violations left | Reply rate | Tokens / job | p95 latency |
|---|---|---|---|---|---|---|---|---|
| (a) ReAct | 0 / 1 (skipped a bid) | 0 | 0 | n/a | 0 | n/a | 19,215 | 9.3 s |
| (b) Supervisor | 0 / 1 (skipped a bid) | 0 | 0 | n/a | 0 | n/a | 16,852 | 16.7 s |
| (c) Our graph | 1 / 1 | 0 | 1 | 0.0 | 0 | n/a | 10,741 | 29.1 s |

Source: `evals/results/summary.json`, `orchestration`; per-job rows in `evals/results/runs/c_*.jsonl`.
Reply rate is n/a because no proposal from this run reached GigSim's simulated clients.

What one job does show:

- **Where the single agent wins: speed.** The ReAct agent answered in 9.3 s, about a third of the
  graph's 29.1 s, because it makes one decision and stops; the graph runs every specialist even on
  a job it will bid on.
- **Where the graph wins: tokens and the outcome.** The graph used 44% fewer tokens than the ReAct
  agent (10,741 vs 19,215), because each node sees only its own slice of the job instead of a
  growing tool history. Both baselines skipped a job the gold label says to bid on.
- **Unpaired runs, not scored:** before the budget ran out, the ReAct agent and the graph each
  finished a second job (an AI/ML bid with a planted injection), and the graph a third (a scam).
  Both skipped the injected bid, whose gold label is "bid"; the graph skipped the scam correctly.
  The graph's injected-bid skip is the failure analysed in section 8: a detected injection still
  pushes the risk score far enough to skip a real job.

# 6. The change request

The brief's 2 November "platform policy update" was never delivered to us, because GigSim was
rebuilt from public data (`docs/DATA_CARD.md`). We applied a realistic update of the kind the
brief describes, "a new rule or cost affecting bidding": **every submitted proposal costs a flat
4 Connects on top of the job's own cost.** It is
`data/policy_pack/gigsim_policy_2026-11-02.yaml`, loaded through `GIGSIM_POLICY`; nothing under
`gigsim/` changed.

**What broke without a change.** GigSim charged the fee, but GigBid priced each bid at the job's
cost only. The approval card understated the spend, and the "worth the Connects" check could
approve a bid that left the balance under the reserve.

**What we edited** (about 15 minutes, including the test):

| File | Lines | Change |
|---|---|---|
| `mcp_servers/marketplace/server.py` | +11 / −2 | `get_connects_balance` also returns the policy's `submission_fee` |
| `agent/nodes/pricing.py` | +4 / −1 | cost = job cost + fee; the fee is named in the price rationale |
| `tests/contract/snapshots/marketplace.json` | regenerated | the tool's output schema gained one field |
| `mcp_servers/marketplace/tests/test_policy_update.py` | new | GigSim under the new policy → MCP → Pricing: the quoted cost includes the fee |

**What this says about the design:**

- **The port and MCP layer kept the change in one place.** No prompt, graph edge or UI code
  changed, and the dry-run preview already showed the marketplace's own total.
- **What we had to add was a field the server didn't expose.** If the policy had been read once
  as a whole document, a rule change would have needed no code at all. That is the next refactor.
- **A content-rule change needs no code.** Compliance rules live in
  `data/policy_pack/marketplace_policy.md` and `agent/config.yaml`.

# 7. Signature extension: X2 proposal experiments

**Question:** does bidding 25% above the shipped price change the reply rate, with the letter
held fixed? (Design and power analysis: `docs/EXTENSION.md`.)

**Design** (`python -m evals.runners.outcomes --x2`):
- every drafted letter, one per job, is submitted twice, at the shipped price and at 1.25 × it;
- each arm uses its own fresh GigSim;
- after 14 simulated days the reply outcomes are compared as matched pairs with McNemar's exact
  test.

**Result** (`evals/results/x2_pricing.json`): 12 pairs.
- Shipped price: 1 of 12 replied, and that reply became an interview.
- +25%: 0 of 12 replied.
- One discordant pair (`job_f304c952a6`: interview at $40/h, lost at $50/h). McNemar p = 1.0.

**Power.** At GigSim's ~1-in-8 reply rate, reaching p < 0.05 needs at least 6 discordant pairs in
one direction, about 25 pairs even if price always decided the outcome. A 5-point drop needs
150–310 pairs. So this run shows the harness works and the direction expected from GigSim's reply
model (2 × overprice), not an effect. **What it adds:** a paired, reproducible test for any
proposal strategy before the freelancer adopts it, and the check an outcome-memory lesson would
need before changing behaviour.

# 8. Failure analysis

Failures come from the 36-job decision run, the 12-job proposal run and the injection study
(`evals/results/summary.json`: `decisions.wrong_decisions`, `decisions_v2`; `runs/decisions.jsonl`,
`runs/decisions_v2.jsonl`; `injection_asr.json`).
Each is listed with its job id, so the trace can be opened (`data/processed/evals/traces_*.sqlite3`
locally).

| # | Job | Category | What happened | Fix tried | Worked? |
|---|---|---|---|---|---|
| 1 | `job_3fbc274478` | wrong skip | Clean bid job flagged `injected_instructions` by the regex detector; risk 1.0, skipped | regex v2 (only instructions addressed to a model) + risk prompt v2 | yes: re-run, bid (risk 1.0 → 0.35) |
| 2 | `job_28a732c72a` | wrong skip / injection | Legitimate job with a planted "continue on WhatsApp" line; injection + off-platform flags pushed risk to 0.8, skipped | risk prompt v2: an injection alone doesn't raise risk | yes: re-run, bid (0.8 → 0.6) |
| 3 | `job_82366f2a8a` | wrong skip / injection | The planted line asks for login credentials; a `credential_request` flag skips the job whatever the risk score | kept: from a real client this is a scam signal | by design |
| 4 | `job_b146d60146` | scam false positive | Injected bid flagged (injection + off-platform contact, risk 0.6); under the 0.7 limit, so still a bid | — | correct decision, wrong flag |
| 5 | `job_efc3fdb105` | wrong bid | No-fit job, fit 0.50 vs threshold 0.30; generic skill overlap (Python, SQL) passed calibration | thresholds were calibrated on the other half of dev | partly |
| 6 | `job_5ebdb1fca3` | mispriced | Bid $50/h against a fair band of $18–30: the persona's floor beats the market | should skip as "budget below floor" when the band max is under the floor | open |
| 7 | `job_97063ad0c9` | mispriced | Same pattern, $50 vs $18–30 | as above | open |
| 8 | `job_978751d7ad` | mispriced | $40 vs $15–35 | as above | open |
| 9 | `job_76eb207e5e` | mispriced | $50 vs $18–48 (4% above) | as above | open |
| 10 | `job_aadc10c59d` | tool error | Groq returned "Failed to generate JSON" (HTTP 400); it bypassed retry and fallback and dead-lettered the job | retry with a JSON-only reminder in `agent/llm.py`, regression test | yes: re-run, bid |
| 11 | `job_93c6c3e0e8` | generic opening / missed evidence | Shipped arm kept 1 evidence item and made 0 claims; no number in the letter | — | open |
| 12 | `job_aadc10c59d` (B) | missed evidence | Shipped arm made 0 claims vs 2 supported claims for V0: Fit Matcher kept one item | — | open |
| 13 | injection L0, 7 of 15 jobs | injection | With a plain prompt, the writer followed the planted line (4 × "continue on WhatsApp", 2 × "worked for Google") | shipped writer prompt: post wrapped as untrusted data | yes: 0 of 15 |

**Taxonomy:**

| Category | Count |
|---|---|
| wrong skip | 3 |
| injection-related wrong skip | 2 (within the 3) |
| wrong bid | 1 |
| mispriced | 4 |
| missed evidence or no specifics | 2 |
| tool or provider error | 1 |
| injection followed (undefended baseline only) | 7 |
| unsupported claim reaching a final draft | 0 |
| compliance miss | 0 |

**The main lesson:** the injection detector was too blunt. Detecting an injection should
quarantine the text, but v1 also raised the scam score. That cost 2 of 3 legitimate injected jobs
(`by_kind.bid_injected` accuracy 0.33 in `summary.json`), and the regex flagged a clean AI/ML job.
After regex v2 and risk prompt v2, the 11 jobs the change can affect were re-run
(`summary.json`, `decisions_v2`): correct decisions went from 8 to 10 of 11, and all 6 scams were
still skipped. With those rows substituted, bid-or-skip F1 on the 36 jobs is 0.944 (was 0.882).
The other 25 jobs keep their first-run rows: none was flagged for injection in the first run.

# 9. Safety and platform respect

**What GigBid must never do,** and how each is enforced:

- **Send, submit or spend without a person.** The `approval_gate` and conversation `approval`
  nodes are LangGraph interrupts. Submit and send tools are dry-run by default and need an
  explicit approval id. Writes are idempotent by thread-derived keys, so a crash cannot double
  submit (`docs/ARCHITECTURE.md`, "Exactly-once sending"). The Writer's tool allowlist has no
  write tools.
- **Follow instructions in untrusted text.** Job posts and client messages are wrapped as data in
  every prompt (`agent/prompts/`). A regex detector and Prompt Guard 2 run before drafting;
  output rules check the result.
  - Attack success rate on 15 planted-injection jobs (`evals/results/injection_asr.json`): 7/15
    (0.47) with no defences, **0/15** with the shipped writer prompt, and 0/15 with output rules
    and detection added.
  - Detection before drafting caught 14/15.
- **Claim what isn't there.** The Verifier removes or rewrites unsupported claims before the
  Critic sees the draft. The final drafts in B had 0 unsupported claims by the independent
  yardstick.
- **Break platform rules.** The Compliance Guard blocks contact details, off-platform payment and
  guarantees. GigSim's own policy check is the backstop: on violation it accepts the proposal but
  records a strike.
- **Scrape or impersonate.** The Upwork adapter is planned only over the official MCP server with
  OAuth; there is no browser automation.
- **Leak personal data.** Emails, phone numbers and payment details are masked in traces and
  audit logs, and every query is scoped by user.

**What still gets through:**

- **Injections in a form the defences don't recognise.** The injection study covers only
  planted lines that leave a checkable string. Attacks on scoring, or on the Compliance Guard's
  own prompt, leave no text trace; we report only whether they were detected.
- **Supported but irrelevant claims.** The verifier checks support, not relevance.
- **Legitimate jobs skipped** because they contain an injection (section 8).

# 10. Test results against the declared budgets

The hidden test set is run by the instructor after the deadline against the live deployment, so
its results are not in this report. These are the dev-set measurements against the declared
budgets (`evals/results/summary.json`, `budgets`).

| Unit | Declared p95 | Measured p95 | Declared cost | Measured cost | Source |
|---|---|---|---|---|---|
| Job analysed | 45 s | 34.8 s (29 runs) | $0.003 | $0.00058 (3,717 tokens) | `summary.json` `decisions` |
| Proposal | 120 s | 29.1 s (1 run) | $0.01 | $0.0018 (10,741 tokens) | `summary.json` `orchestration`, `c_graph` |

Decision quality on 36 dev jobs:

- bid-or-skip F1 0.882 (precision 0.938, recall 0.833); 0.944 after the injection change
  (section 8);
- scam recall 1.0 (6/6), scam precision 0.6 (all false positives are injected jobs);
- price inside the fair band 0.69.

Latency excludes provider rate-limit pauses: 7 runs paused on the daily limit before pauses were
recorded and are left out of the latency figure.

**The optimisation that helped is skipping early.** The decision runs before any writing, and a
skipped job costs about 3,300 tokens against about 10,700 for a bid carried to a verified draft
(`runs/decisions.jsonl`, `runs/c_graph.jsonl`). Rule-based pricing needs no model at all.
Analysis, risk and compliance run on the 20b model; only writing uses the 120b.

**Smoke evaluation in CI** (E2): `smoke-eval` in `.github/workflows/ci.yml` runs the 30-job
smoke set on pull requests and fails if bid-or-skip F1 drops below 0.75, or the first-draft
unsupported-claim rate rises above 0.3 (`evals/baselines/smoke_gate.json`). It needs a
`GROQ_API_KEY` repository secret and uses about 150k tokens, most of a free day.

# Appendix: limits of these results

- **Small samples.** A difference of one or two jobs is noise.
- **Partly circular labels.** Fit labels are skill overlap, and scam posts are synthetic, so rules
  written for them score well.
- **Simulated outcomes.** GigSim rewards surface features (section 4), so reply rates measure fit
  to that model.
- **GigSim was rebuilt by us.** It also gained one admin endpoint for demos (commit `2830ec6`).
