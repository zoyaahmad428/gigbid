# Data Card: GigSim job stream, clients and persona corpora

**Version** 1.0.0 · **Seed** 4016 · **Built by** `data/pipeline/run.py` · **Build report** `data/pipeline/reports/build_report.json`

Every number in this card comes from `data/pipeline/reports/build_report.json` or
`data/pipeline/reports/sources.json`. Rebuilding with the same seed gives byte-identical output
(checked by hashing every output file across two builds).

## 1. What this dataset is for

The instructor's GigSim data and persona corpora were not available, so this dataset
reproduces them. Real Kaggle data is used wherever a suitable public dataset exists, and synthetic
data fills the gaps. It drives:

- the job stream the Scout reads, with duplicates, scams and planted prompt injections;
- the client histories the Risk Assessor reads, plus hidden preferences that only the simulated client sees;
- the three portfolio corpora that the Portfolio MCP server and the RAG variants index;
- the gold-labelled dev set used for evaluation.

It is **not** the instructor's hidden test set, and results on it should not be presented as
results on that set.

## 2. Contents at a glance

| Output | Path | Records | Real | Synthetic |
|---|---|---|---|---|
| Historical jobs | `data/gigsim/jobs_historical.jsonl` | 1,500 | 1,500 | 0 |
| Live stream | `data/gigsim/jobs_live.jsonl` | 488 | 400 | 88 (40 duplicates, 24 scams, 24 injections) |
| Clients (public) | `data/gigsim/clients.jsonl` | 2,251 | 1,613 | 638 |
| Client hidden preferences | `data/gigsim/_hidden/clients_hidden.jsonl` | 2,251 | 0 | 2,251 |
| Live-stream labels | `data/gigsim/_hidden/live_labels.jsonl` | 488 | n/a | n/a |
| Market price statistics | `data/gigsim/market_stats.json` | 4 categories | all | 0 |
| Scam lexicon (statistics only) | `data/gigsim/scam_lexicon.json` | 60 phrases | all | 0 |
| Technology vocabulary | `rag/tech_terms.json` | 229 terms | all | 0 |
| Persona corpora | `data/personas/{ai_ml,fullstack,data_analyst}/` | see §6 | 0 | all |
| Dev set | `evals/datasets/dev_{persona}.jsonl` | 200 per persona | 172 jobs each | 28 jobs each |

"Real" means the record's main content (job title and description, or client signals) comes from a
Kaggle dataset. A real record can still have some synthetic fields; these are listed in
`provenance.synthetic_fields` (see §5).

## 3. Sources and licences

| Dataset (Kaggle) | Licence | Used for | Not used |
|---|---|---|---|
| [asaniczka/upwork-job-postings-dataset-2024-50k-records](https://www.kaggle.com/datasets/asaniczka/upwork-job-postings-dataset-2024-50k-records) | ODC-By | Job title, description, budget, country, timestamp (Dec 2023 to Feb 2024) | `link` (only its id, for de-duplication) |
| [asaniczka/all-jobs-on-upwork-200k-plus](https://www.kaggle.com/datasets/asaniczka/all-jobs-on-upwork-200k-plus) | ODC-By | Market budget and hourly-rate statistics (733,128 postings, Feb to Jul 2024) | No descriptions in this dataset, so no job text |
| [ahmedmyalo/upwork-freelance-jobs-60k](https://www.kaggle.com/datasets/ahmedmyalo/upwork-freelance-jobs-60k) | Apache-2.0 | Job text, skill tags, budget, experience level, duration, Connects cost, and **client signals** (payment verified, total spent, rating, feedback count, country) (Feb to Mar 2023) | `Applicants_Num`, `Enterprise_Client` (100% empty), `highlight` |
| [asaniczka/freelance-contracts-dataset-1-3-million-entries](https://www.kaggle.com/datasets/asaniczka/freelance-contracts-dataset-1-3-million-entries) | ODC-By | Hourly rates and totals actually paid (138,281 contracts from 2015 to 2024); real contracts attached to clients as past history | `freelancer_id` |
| [sarveshchhetri/freelancer-success-earnings-and-churn-prediction](https://www.kaggle.com/datasets/sarveshchhetri/freelancer-success-earnings-and-churn-prediction) | Apache-2.0 | Persona priors: job-success score, completed jobs | `hourly_rate_usd`, which is implausible (see §9) |
| [adityammantri/trial-1](https://www.kaggle.com/datasets/adityammantri/trial-1) | **Unknown** | Aggregate n-gram statistics of fraudulent vs genuine ads only | All text. Nothing from it is redistributed. |

Licence strings were read from the Kaggle CLI at download time (`sources.json`).

**Attribution.** ODC-By and Apache-2.0 require credit. This dataset contains material derived from
the datasets above by asaniczka and ahmedmyalo on Kaggle, under their licences. Derived records
name their source in `provenance.source_dataset`.

**Platform terms.** The three Upwork job datasets were scraped by their publishers, not by us.
GigBid never scrapes (assignment rule R9); we only use these published dumps offline. Job text is
kept for simulation and research only and is PII-masked (§4).

## 4. Preprocessing of real data

Code: `data/pipeline/clean/`. The counts below come from the build report.

**Job postings** (`clean/jobs.py`). We loaded 63,950 rows from the 60k dataset and 44,829 from the
50k dataset (the 50k count excludes 8,229 rows with no budget information at all). Then:

1. Strip HTML, decode entities, normalise whitespace, and repair run-together sentences ("team!We" → "team! We").
2. Remove 2,610 duplicates that share a job URL id (the same posting appearing in both scrapes).
3. Drop 14,856 posts whose description is under 200 characters.
4. Drop 191 posts that aren't English (fewer than 95% ASCII letters).
5. Remove 3,275 re-posts of the same job (normalised title plus the first 300 characters of the description).
6. **Mask PII** (15,666 replacements): emails, URLs, phone numbers, card and IBAN numbers and @handles become `[EMAIL]`, `[URL]` and so on. A match counts as a phone number only if it has 10–15 digits (or starts with `+`) and is not a date or a numeric range. An earlier version masked 548 dates and ranges such as "1000-2000 words" as phones.
7. **Extract skills.** Explicit tags (60k only) and skills mentioned in free text are mapped to 57 standard skills (`taxonomy.py`). Ambiguous aliases ("R", "ML", "LoRa", "rest") are accepted only from tags, never from free text.
8. **Assign category and persona fit.** A job fits a persona if it names at least two of that persona's skills, or one in its title.
9. **Normalise budgets** to USD fixed or hourly. Impossible values become unknown (hourly below $3 or above $500, fixed below $5 or above $250k), and reversed ranges are swapped.
10. **Approximate the 60k post times.** The 60k dataset gives only relative ages ("2 days ago") from a scrape in Feb to Mar 2023, so ages are anchored to 2023-03-15 and those `posted_at` values are flagged as approximate.

The result is a pool of 87,847 cleaned postings: 1,466 AI/ML, 10,993 full-stack, 2,509 data analysis
and 72,879 other. The historical, live and dev jobs are drawn from this pool **without overlap**.

**Market statistics** (`clean/market.py`). These are percentiles (p10 to p90) per category, covering:

- posted fixed budgets and hourly midpoints (the 200k dataset);
- contract hourly rates and total paid (the contracts dataset, filtered to start dates in 2015 to 2024 because it contains dates up to 2055).

Both datasets have titles only, so their category comes from skills in the title.

**Scam lexicon** (`clean/fraud_lexicon.py`). This lists the bigrams and trigrams most over-represented in
fraudulent ads (smoothed log-odds). 10,000 of the 10,757 fraudulent rows follow one machine-generated
template, so they were counted and excluded. One ad per company was kept, to stop boilerplate
dominating. 603 fraudulent and 11,399 genuine ads remained. Only phrases and counts are published.

**Technology vocabulary** (`clean/tech_terms.py`). The claim verifier uses it to notice a draft naming
a technology that no evidence mentions ("built with Kotlin"), including technologies outside the
57-skill persona taxonomy. It comes from the real skill tags (`Category_1..9`) of the Upwork 60k
dataset. A tag is kept if it is used on at least 20 jobs, has at most 3 words, and job descriptions
write it in its tagged capitalisation at least 70% of the times it appears. Technology names are
proper nouns ("We use Flutter"), while generic tags such as "Market Research" mostly appear in lower
case. Tags containing `.`, `+` or `#` (Node.js, C++, .NET) are always kept, and natural languages are
always dropped. 229 of 1,242 candidate tags remain. Only the tag names are published.

## 5. Provenance tagging

Every record has a `provenance` object:

```json
{"source": "real|synthetic", "source_dataset": "<kaggle slug or 'generator'>",
 "generator": "synth.adversarial.injection@1.0.0", "derived_from": "<original job_id>",
 "synthetic_fields": ["client_id", "screening_questions", "connects_cost"]}
```

- **`real`**: the core content is from a Kaggle dataset. `synthetic_fields` lists anything filled in by a generator:
  - `client_id`: always synthetic, because no source links jobs to clients;
  - `screening_questions`: added to 45% of jobs, since no source has them;
  - `connects_cost`: for 50k-dataset jobs, set to the median real 60k Connects cost for the same budget bracket;
  - `posted_at`: re-timed for the live stream, or approximate for 60k jobs.
- **`synthetic`**: generated. If the record was built from a real one (an injected copy or a duplicate), `derived_from` points to the original.
- **Client past contracts** are real contract rows (`source: real` on each item). Which client they belong to is synthetic; it's listed in `synthetic_fields` as `past_contracts linkage`.

## 6. Synthetic data

Code: `data/pipeline/synth/`. All generators are template-based and seeded, and no LLM was used:
output is reproducible, costs nothing, and every claim can be checked against ground truth.

**Personas** (`personas.py`, `persona_content.py`). There are three fictional freelancers. Their names,
employers and universities are fictional and marked as such in each CV.

| | AI/ML engineer | Full-stack developer | Data analyst |
|---|---|---|---|
| Projects (case studies) | 17 (20 docs) | 16 (19 docs) | 22 (25 docs) |
| Repo READMEs plus code files | 17 + 17 | 16 + 16 | 22 + 22 |
| Client reviews | 8 | 14 | 15 |
| Past proposals (won/replied/ignored) | 150 (13/31/106) | 150 (20/28/102) | 150 (14/31/105) |
| Hourly rate (prior) | $55 | $40 | $50 |

How the personas are built:

- **One source of truth per project.** Each project is built from an archetype × client industry × dates × metric values, and stored in `facts.jsonl`. Every text about that project (case study, README, code, CV bullet, review, proposal mention) is rendered from the same facts. `validate.py` fails if a case study states a number that isn't in its facts.
- **Built to defeat naive RAG** (the assignment's requirement):
  - The same project is described three ways: a case study in one of three styles, a README, and a CV bullet. Three projects per persona also get a blog-style note that never repeats the title.
  - Some skills are implied rather than named: they appear only in code or in descriptions of the work (`skills_implied` in the evidence index).
  - Code and prose are mixed in one case-study style.
  - One "decoy" repo per persona has a README that undersells it ("mostly for my own reuse"), so only the code shows its relevance (retrieval variant V5).
- **Profile priors.**
  - Hourly rate: the p75 contract hourly rate for the persona's category.
  - Minimum fixed budget: the p25 posted fixed budget.
  - Job-success score and completed jobs: from the freelancer-success dataset (sources are listed in `profile.json`).

**Past proposals** (`proposals.py`). Each one is written for a real historical job: 80% from the
persona's field, 20% from outside it. Proposals cite only projects that share at least one skill with
the job, and only state true facts.

Outcomes are sampled with this formula:
`p(reply or win) = sigmoid(c + 2.2·fit + 1.0·specificity − 0.9·generic_opening − 1.0·overprice)`
and `p(win | engaged) = k·(0.5 + 0.5·fit)`.

`c` and `k` are calibrated so expected rates match config (8% won, 22% replied). In the result, won
proposals have a mean fit of 0.81 and 19% generic openings; ignored proposals have 0.56 and 41%. That
gives outcome-aware ranking a real but noisy signal.

**Clients** (`gigsim.py`). Most jobs from the 60k dataset get a client built from that posting's
real signals (payment verified, spent, rating, feedback count, country). Jobs from the 50k dataset
have no client data, so they get synthetic clients. For those:

- 35% of their jobs go to a client who has already posted, to create repeat posters;
- payment-verified status is set at 66%, matching the 60k rate;
- hires and spend are sampled from a long-tailed distribution.

**Hidden preferences** (synthetic for every client) are:

- 1–2 must-have skills taken from the client's job;
- a budget ceiling of the job maximum × U(1.0, 1.25), or the market p75 when the budget is unknown;
- a dislike of generic openings (65% of clients) and a preference for short proposals (40%);
- responsiveness, based on payment verification and rating.

**Live stream** (`gigsim.py`, `adversarial.py`). 400 real held-out jobs are re-timed into
2 to 16 Nov 2026. We then add:

- **40 duplicates** (10%): half exact re-posts, half paraphrased (title suffix, phrase swaps, paragraph reordering, an added greeting). Each is posted 2–96 hours after its original.
- **24 scams** (6%) from six patterns: free trial work, off-platform payment, off-platform contact, overpayment cheque, credential fishing, and too-good-to-be-true. The lure lines are hand-written from the themes in the scam lexicon. All contact details are fake, using `example.com`, `.test` domains and `555-01xx` numbers.
- **24 prompt injections** (6%) from eight types: ignoring instructions, fake system messages, data exfiltration, price manipulation, approval bypass, fabrication requests, off-platform redirects, and hidden markers. They are planted in the description (about 70%), a screening question (about 22%) or the title (about 8%).

Labels are kept separately in `_hidden/live_labels.jsonl`.

**Dev set**. Each persona gets 200 jobs:

- 134 jobs from its own category, 12 of them carrying an injection;
- 50 off-topic jobs;
- 16 synthetic scams.

Gold labels:

- `gold_action` follows these rules in order:
  1. scam → skip;
  2. no persona fit → skip;
  3. under 50% of the persona-relevant job skills covered → skip;
  4. a skill the persona doesn't do → skip;
  5. hourly maximum under 60% of the persona's rate, or fixed budget under the persona's floor → skip;
  6. otherwise bid.
- `relevant_projects`: up to 3 projects with the most skill overlap.
- `relevant_evidence`: the best 5 portfolio items from those projects, ranked by job skills covered (named or implied). Recall@5 can therefore reach 1.0.
- `evidence_grades`: graded relevance for nDCG: 2 for the top 5, 1 for other items of those projects and for the CV.
- `fair_price_band`: for fixed jobs with a budget, 0.75–1.0 × the budget; otherwise the category's market p25–p75.

Past proposals are not gold evidence: they show a pitch, not proof of the work.

| | Bid | Skip (no fit / budget / missing skills / scam) |
|---|---|---|
| AI/ML | 100 | 100 (49 / 35 / 0 / 16) |
| Full-stack | 100 | 100 (58 / 22 / 4 / 16) |
| Data analyst | 85 | 115 (70 / 28 / 1 / 16) |

## 7. Splits and leakage controls

- Historical, live and dev jobs are disjoint (validated).
- Hidden client preferences and live labels live only under `data/gigsim/_hidden/`. No public client record contains a hidden field (validated). The Portfolio and Marketplace MCP servers must not read `_hidden/`.
- The dev set uses real jobs that never appear in the job stream, so tuning on the dev set can't leak into stream-based runs.

## 8. Validation

`python data/pipeline/validate.py` (also run at the end of every build) checks:

- the schema of every record;
- `source` and `generator` tags;
- unique ids;
- that every job references a known client;
- that labels match content (exact duplicates are identical and posted later; injected and scam jobs are synthetic with `derived_from`);
- that real text contains no unmasked email;
- that evidence paths exist and no stray files sit in the persona folders;
- that case-study numbers match project facts;
- target sizes;
- that gold evidence ids exist and gold lists have at most 5 items.

Build 1.0.0 status: **OK, 0 problems**.

## 9. Limitations and known issues

- **Time drift.** Real jobs are from 2023 to 2024; the live stream is re-timed to November 2026. LLM-era jobs (RAG, agents) are under-represented compared with 2026.
- **Selection bias.**
  - The 60k scrape used four search keywords (Developer, Marketing, 3D, Data science), which affects the category mix.
  - The 50k dataset excludes postings without budgets.
  - Only English postings are kept.
  - Only 1,466 cleaned AI/ML postings exist, so AI/ML draws come from a small pool.
- **The skill taxonomy is keyword-based** (57 skills). It misses skills it has no alias for (Swift, Unity, Salesforce and others count as "Other") and can still mis-tag. Persona fit is a rule, not a human label.
- **Client linkage is synthetic.** No source ties a job to a client's full history. Client signals for 60k jobs are real; for 50k jobs they are fully synthetic. Past contracts are real contracts attached to clients at random. Jobs posted, hires, member-since date and industry are synthetic.
- **Outcomes are simulated.** Proposal outcomes and hidden preferences come from a formula we chose, not from real client behaviour. A system tuned to this data learns our model of clients. Don't present simulated reply rates as real-world rates.
- **Persona corpora are template text.** They are cleaner and more uniform than real portfolios, and repeated archetypes can pair a project with an odd industry (for example "stock planning" for a real-estate client). Template phrasing may make retrieval easier or harder than real data.
- **Problems with the source data:**
  - `freelancer_success` looks synthetic itself: the median hourly rate is $280 in every category, and the medians barely differ between categories. Only job-success and completed-jobs percentiles are used, and they should be treated as weak priors.
  - In `trial_1`, 10,000 of 10,757 fraudulent rows are one template with random fill-ins, the text has punctuation stripped, and the licence is unknown. It informs scam themes only.
  - The contracts dataset has impossible dates (up to 2055) and zero-hour hourly contracts. Rows outside 2015–2024 are filtered out; zero-hour rows remain in past-contract history.
- **Duplicates and injections are simple.** Paraphrases are rule-based, so a strong near-duplicate detector will find most of them. Injection phrasings come from a fixed list of 16 lines, so a defence can overfit to them. Write new attacks when measuring attack success rate.
- **PII masking is regex-based.** It can miss obfuscated contacts (for example "john at gmail dot com") and can over-mask number-like strings.

## 10. Reproduce

```bash
python -m venv .venv && .venv/bin/pip install -e ".[data]"
make data-download   # Kaggle CLI; public datasets need no token; writes reports/sources.json
make seed            # cleans, generates, validates (~2 min from raw; ~10 s with cached interim files)
make data-validate
```

`data/raw/` and `data/interim/` are gitignored. Sizes, rates and the seed are in
`data/pipeline/config.yaml`.
