# Signature extension: X2, proposal experiments

**Question.** Does bidding 25% above the shipped price change the reply rate, holding the
letter fixed?

The shipped Pricing node bids at the market's upper quartile, capped at the client's posted
maximum and never under the freelancer's floor (`agent/nodes/pricing.py`). A freelancer will
reasonably ask whether a higher bid signals quality or just loses the job. The experiment
separates price from everything else.

## Design

`python -m evals.runners.outcomes --x2` (`make x2`):

1. **Letters.** Take every proposal GigBid has drafted in the evaluations: the 12 jobs of each B
   arm and the bids from question C (`--x2-sources`). Keep one letter per job, because a GigSim
   account can apply to a job only once.
2. **Two arms, same letters.**
   - Arm A submits each letter at the shipped price.
   - Arm B submits the same letter at 1.25 × that price.

   Each arm uses its own fresh GigSim (own state, chaos off), so the arms can't interact.
3. **Outcome.** Move the simulated clock forward 14 days and record whether the client replied.
   This is GigSim's definition: any client message on the proposal.
4. **Test.** Each job is a matched pair, so the test is McNemar's exact test on the discordant
   pairs: jobs where only one arm got a reply. A two-sample test would ignore the pairing.

GigSim's client decisions are deterministic per proposal content, so a re-run reproduces the
result exactly. The harness plays the approving human only inside these private simulators.

## Results

From `evals/results/x2_pricing.json` (12 pairs: the 12 B jobs; the question C runs had no drafted
bid yet when X2 ran, so they added none):

| | Arm A: shipped price | Arm B: +25% |
|---|---|---|
| Submitted (rejected at submission) | 12 (0) | 12 (0) |
| Replied | 1 of 12 | 0 of 12 |
| Interview | 1 of 12 | 0 of 12 |
| Hired | 0 | 0 |

One discordant pair, in A's favour: `job_f304c952a6` got an interview at $40/h and was lost at
$50/h. McNemar's exact p = 1.0.

**Reading.** The direction matches GigSim's public reply model, which subtracts 2 × the
fraction a bid is above the client's ceiling (`gigsim/client_sim.py`). The sample can't
show an effect of this size, as the power section below explains. The finding is the harness: one
command gives a paired price test, and the same command with 150–310 drafts would answer it.

## Power: what this sample can and cannot show

GigSim's base reply rate is about 1 in 8. With *n* pairs, the expected number of discordant pairs
is at most about 0.25 *n*. McNemar's exact test needs at least 6 discordant pairs, all in one
direction, to reach p < 0.05 (2 × 0.5⁶ = 0.031). That takes about 25 pairs even if the premium
always flips the outcome, and far more if it only sometimes does.

- **With ~15 pairs, the experiment can only rule out very large effects.** A non-significant
  result here is not evidence that price doesn't matter.
- **Detecting a 5-point drop** (12% → 7%) with 80% power at α = 0.05 needs about 150 pairs if
  every discordant pair runs one way. It needs about 310 if the premium flips 10% of outcomes in
  both directions. That is 150–310 drafted proposals: one to two weeks of one model's free daily
  token limit.

The harness takes any number of sources, so a larger run is a matter of more drafts.

## What it adds to GigBid

- **A repeatable way to test a proposal strategy before the freelancer adopts it.** Price is the
  first one; a letter style or an opening would use the same harness with a different
  transformation of arm B.
- **A feed for outcome memory.** The outcome-lesson rule (`agent/memory/promotion_rules.py`)
  promotes a lesson only after a category has 5 resolved proposals and a 15-point difference. The
  same paired comparison is how such a lesson would be checked before it changes behaviour.
