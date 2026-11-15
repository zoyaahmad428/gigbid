# AI usage disclosure

## Tool

**Claude Code** (Anthropic's coding agent; Claude Opus models), run in VS Code on WSL, wrote most of the code,
tests and documentation in this repository. It ran the commands, tests and evaluations in my terminal.
I made every commit and push myself.

**Groq's API** (gpt-oss-20b, gpt-oss-120b, qwen3.8-27b, Llama Prompt Guard 2) is the product's
own model provider, not a coding aid. It is listed under Models in the README.

## What I decided

- The data sources: six Kaggle datasets on Upwork jobs, contracts and fraud. GigSim and the personas
  are built from them, because the starter kit shipped without the instructor's data.
- The repository, the order of work (data → MCP servers → RAG → agents → UI → ops → evaluations
  → write-ups) and when each step was ready to commit.
- The model provider (Groq) and, when paid upgrades were closed, running every evaluation on
  the free tier instead of waiting.
- The 30 human labels that calibrate the LLM judge are mine (`evals/judge/human_labels.json`).
- <!-- TODO (you): what you reviewed line by line, what you changed by hand, what you'd defend in the viva -->

## What the AI did

Wrote the data pipeline, GigSim, the three MCP servers, the RAG variants and verifier, both graphs,
the API, the web app, the worker, Docker/CI, the evaluation harness and first drafts of these docs.
It also designed most experiments; their results are in `evals/results/`.

## Mistakes caught and corrected

Each of these was a wrong suggestion or a bug the AI introduced. Tests, evaluation numbers or
reading the output caught them.

| Where | What was wrong | How it was caught | Fix |
|---|---|---|---|
| Data | PII masking turned dates and number ranges ("2023-03-15", "10-15") into `[PHONE]` | reading masked job posts | `looks_like_phone`: 10–15 digits, not a date or range; 15,666 replacements redone |
| Data | `trial_1` fraud rows treated as real scam posts; 10,000 of 10,757 are one template | inspecting the source | kept only aggregate statistics plus curated lures |
| GigSim | `provenance` and `persona_fit` fields leaked scam and injection labels through the public API | a test on the API output | stripped from responses |
| RAG | 83 of 100 top-5 slots went to past proposals, not portfolio evidence | retrieval evaluation | portfolio pool by default |
| RAG | Ran two model-loading jobs in parallel on a 5.8 GB machine | out-of-memory kill (exit 137) | evaluations run one at a time |
| Verifier | Citation numbers ("[3]") counted as claimed figures; 42% of cited true claims marked unsupported | verifier evaluation | citation and evidence-id patterns |
| Agents | The node wrapper swallowed LangGraph's interrupt exception, so the approval gate never paused | graph test | re-raise `GraphBubbleUp` |
| Agents | The Critic pushed the Writer towards fabricating figures | reading drafts | Critic prompt v2 with the requirement-to-evidence matrix |
| Agents | Fit thresholds were hand-set; most gold-bid jobs ended at watch or skip | dev-set run | `evals/runners/fit_calibration.py` calibrates them |
| Memory | Edit memory learned `avoid: fastapi` from one rewrite | inspecting learned rules | rule extraction fixed |
| Evals | A Groq "Failed to generate JSON" 400 bypassed retry and fallback and dead-lettered a job | the decisions run (`job_aadc10c59d`) | retried in `agent/llm.py`, regression test |
| Evals | The rate-limit parser read "577ms" as 577 minutes | unit test | parser rewritten |
| Evals | The "no defences" injection baseline still contained the safety rules, so it measured nothing | reading the prompt | naive prompt `eval_naive_writer.md`; first run discarded |
| Evals | The 30-job smoke set left out the scam jobs | reading the job list | slice fixed |
| Evals | Wrong config key (`max_risk`) for the scam threshold | review against `config.yaml` | `maximum_risk_score` |
| Evals | p95 latency included hours of rate-limit sleeps (1,260 s) | implausible number | pauses recorded and excluded (`agent/llm.py: rate_waited`) |
| Evals | A job queue waited for "the decisions run to finish" with `pgrep`, which matched the queue itself; it idled for 4 hours | progress check | wait removed; runs interleaved instead |
| Evals | Qwen was proposed for tool-calling baselines; on Groq it corrupts tool names (`get_client buzz`) | first baseline run | tool-calling studies use gpt-oss only |
| Evals | The judge prompt passed the job post in undelimited, so the judge obeyed planted instructions and marked letters down for ignoring them; it was also a point too lenient (kappa 0.29) | my 30 hand labels | judge v2 (`agent/prompts/judge_v2.md`): post marked untrusted, fit and grounding checks; kappa 0.61, reported as optimistic |

## What I'd check first if something looks wrong

The numbers in `evals/results/` come from runs on a free API tier with small samples (see
`docs/EVALUATION.md`). The judge's agreement with my labels is reported, not assumed.
