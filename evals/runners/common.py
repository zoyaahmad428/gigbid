"""Shared pieces of the step-7 evaluations: fixed job sets, resumable result logs, eval GigSims.

Job sets are drawn once from the labelled dev sets with a fixed seed and saved to
evals/datasets/eval_sets.json, so every reported number traces to a committed list of jobs.
Sizes are what the free Groq tier allows (200k tokens per model per day); see docs/EVALUATION.md.

Each system under test gets its own GigSim instance with fresh state (own port and state file,
chaos off), so one system's proposals never block or influence another's.
"""
from __future__ import annotations

import contextlib
import json
import os
import random
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "evals/results"
WORK = ROOT / "data/processed/evals"                      # checkpoints, traces, GigSim state of eval runs
SETS = ROOT / "evals/datasets/eval_sets.json"
PERSONAS = ["ai_ml", "fullstack", "data_analyst"]
TOKENS = {"ai_ml": "dev-token-ai-ml", "fullstack": "dev-token-fullstack", "data_analyst": "dev-token-data-analyst"}
SEED = 4016


def dev(persona: str) -> list[dict]:
    with open(ROOT / f"evals/datasets/dev_{persona}.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def gold_by_id() -> dict[str, dict]:
    return {x["job"]["job_id"]: x for p in PERSONAS for x in dev(p)}


def user_for(persona: str) -> dict:
    """The evaluation user: the persona's own profile, as onboarding would store it."""
    p = json.loads((ROOT / f"data/personas/{persona}/profile.json").read_text())
    return {"user_id": f"eval_{persona}", "persona": persona, "marketplace_token": TOKENS[persona], "name": p["name"],
            "headline": p["headline"], "hourly_rate": p["hourly_rate_usd"], "min_fixed_budget": p["min_fixed_budget_usd"],
            "autonomy_auto_skip": True, "timezone": "Asia/Karachi"}


def kind_of(x: dict) -> str:
    if x["is_scam"]:
        return "scam"
    if x["gold_action"] == "bid":
        return "bid_injected" if x["has_injection"] else "bid"
    return {"no fit": "skip_nofit", "budget below floor": "skip_budget"}.get(x["skip_reason"], "skip_other")


# Per persona. decisions ⊇ proposals, decisions ⊇ orchestration.
PLAN = {"bid": 5, "bid_injected": 1, "skip_nofit": 2, "skip_budget": 2, "scam": 2}
ORCH = {"bid": 2, "bid_injected": 1, "scam": 1}
INJ_PER_PERSONA = 4


def build_sets() -> dict:
    rng = random.Random(SEED)
    sets: dict[str, list[dict]] = {"decisions": [], "proposals": [], "orchestration": [], "injection": []}
    for p in PERSONAS:
        by_kind: dict[str, list[dict]] = {}
        for x in dev(p):
            by_kind.setdefault(kind_of(x), []).append(x)
        chosen = {k: rng.sample(by_kind[k], n) for k, n in PLAN.items()}
        for k, xs in chosen.items():
            sets["decisions"] += [{"job_id": x["job"]["job_id"], "persona": p, "kind": k} for x in xs]
        sets["proposals"] += [{"job_id": x["job"]["job_id"], "persona": p, "kind": "bid"} for x in chosen["bid"]]
        for k, n in ORCH.items():
            sets["orchestration"] += [{"job_id": x["job"]["job_id"], "persona": p, "kind": k} for x in chosen[k][:n]]
        inj = [x for x in by_kind["bid_injected"] if x not in chosen["bid_injected"]]
        picks = chosen["bid_injected"] + rng.sample(inj, INJ_PER_PERSONA - 1)
        sets["injection"] += [{"job_id": x["job"]["job_id"], "persona": p, "kind": "bid_injected"} for x in picks]
    return {"seed": SEED, "plan_per_persona": PLAN, "orchestration_per_persona": ORCH, "sets": sets}


def job_set(name: str) -> list[dict]:
    if not SETS.exists():
        SETS.write_text(json.dumps(build_sets(), indent=1) + "\n")
    sets = json.loads(SETS.read_text())["sets"]
    if name == "c":                                       # R&D question C: per persona one bid, injected bid, scam
        seen: set = set()
        return [x for x in sets["orchestration"] if (x["persona"], x["kind"]) not in seen
                and not seen.add((x["persona"], x["kind"]))]
    if name == "smoke":                                   # E2's 30-job smoke set: a fixed slice of decisions
        d = sets["decisions"]                             # per persona: 5 bids, 1 injected, 2 no-fit, 2 scams
        return [x for k in range(3) for i, x in enumerate(d[12 * k:12 * k + 12]) if i not in (8, 9)]
    return sets[name]


_ORG = re.compile(r"org_[A-Za-z0-9]{6,}")


class ResultLog:
    """Append-only JSONL keyed by job id: a rerun skips what is done (runs pause on rate limits)."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []

    def done(self, key: str) -> bool:
        return any(r["key"] == key and not r.get("error") for r in self.rows)

    def add(self, row: dict) -> None:
        self.rows = [r for r in self.rows if r["key"] != row["key"]] + [row]
        text = "".join(json.dumps(r, default=str) + "\n" for r in self.rows)
        self.path.write_text(_ORG.sub("org_[redacted]", text))     # provider account ids in error messages


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def eval_gigsim(name: str, port: int | None = None, fresh: bool = False):
    """A private GigSim for one system under test: own port and state file, chaos off."""
    port = port or free_port()
    state = WORK / f"gigsim_{name}.sqlite3"
    WORK.mkdir(parents=True, exist_ok=True)
    if fresh:
        for f in WORK.glob(f"gigsim_{name}.sqlite3*"):
            f.unlink()
    env = {**os.environ, "GIGSIM_CHAOS": "0", "GIGSIM_STATE_DB": str(state)}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "gigsim.app:app", "--port", str(port), "--log-level",
                             "warning"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(url + "/health", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("eval GigSim did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=20)


def admin(url: str, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"x-admin-token": "dev-admin", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def tokens_by_model(trace_db: Path, thread_id: str) -> dict:
    import sqlite3
    db = sqlite3.connect(str(trace_db))
    out: dict[str, dict] = {}
    for model, ti, to, cost, ms in db.execute(
            "SELECT name, tokens_in, tokens_out, cost_usd, ms FROM events WHERE thread_id=? AND kind='llm'", (thread_id,)):
        m = out.setdefault(model, {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0, "ms": 0.0})
        m["tokens_in"] += ti or 0
        m["tokens_out"] += to or 0
        m["cost_usd"] += cost or 0
        m["calls"] += 1
        m["ms"] += ms or 0
    return out


def net_seconds(row: dict) -> float | None:
    """A run's latency without rate-limit pauses. Rows from before pauses were recorded carry
    `rate_limited` when the run log showed a daily-limit pause; their wall time is not latency."""
    if row.get("seconds_net") is not None:
        return row["seconds_net"]
    return None if row.get("rate_limited") else row.get("seconds")


def percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 3)
