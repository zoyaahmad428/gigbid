"""R&D question A: does tool granularity change agent behaviour?

    python -m evals.runners.tool_study --model openai/gpt-oss-120b [--limit 20]

40 tasks with a known correct call (evals/datasets/tool_tasks.json). The model sees one request
and must call one tool. Three tool sets, the same model and system prompt:

  fine     the 21 MCP tools as shipped (names, schemas, descriptions from the servers)
  coarse   3 tools, one per server: marketplace(action, params), portfolio(...), workspace(...)
  best     the 21 tools with rewritten descriptions: when to use it, and what not to confuse it with

Scored per task: tool-selection accuracy (the right tool; for `coarse` the right server and
action), argument accuracy (share of the task's checked arguments that are right: ids and enums
exactly, free text by key terms), and tokens per task. Tasks run one at a time across all three sets,
rotating through the categories, so a run stopped by the token budget is still a paired comparison.
A call to a tool that does not exist is rejected by the provider and scored as a wrong selection. Writes evals/results/runs/tool_<set>.jsonl
and evals/results/tool_granularity.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time

from evals.runners.common import RESULTS, ResultLog

TASKS_PATH = __import__("pathlib").Path(__file__).resolve().parents[1] / "datasets/tool_tasks.json"

COARSE_ACTIONS = {
    "marketplace": ["marketplace_status", "search_jobs", "get_job", "get_client_history", "get_connects_balance",
                    "get_market_stats", "list_proposals", "list_messages", "get_events", "create_proposal_draft",
                    "submit_proposal", "send_message"],
    "portfolio": ["search_evidence", "get_case_study", "get_evidence", "list_skills"],
    "workspace": ["get_free_slots", "create_tentative_hold", "list_holds", "cancel_hold", "create_email_draft"],
}

BEST = {
    "search_evidence": "Search the freelancer's own portfolio (case studies, READMEs, code, reviews, CV, past proposals) "
                       "for proof of ONE requirement or skill. Use to find evidence; use get_evidence/get_case_study "
                       "to read a document you already have the id of. Not for marketplace jobs.",
    "get_case_study": "Read a full case study when you have its project id (proj_...) or a case-study evidence id, "
                      "with the other documents about that project. For any other document id, use get_evidence.",
    "get_evidence": "Read one portfolio document in full by its evidence id (ev_...): README, code, review, CV, "
                    "past proposal or case study. Use to quote or check exact details.",
    "list_skills": "List every skill the portfolio proves, with how many projects show it. Use for 'what can I "
                   "show / what skills do I have'; not for finding one specific requirement.",
    "marketplace_status": "Check whether the marketplace connection is up and which policy version applies. "
                          "Use only for connectivity/status questions.",
    "search_jobs": "Find marketplace JOBS by keywords, category, skills, budget type or date. Use status='historical' "
                   "for closed past jobs (price research). Not for my proposals, and not for price statistics.",
    "get_job": "Read one job post in full by job id (job_...): description, budget, screening questions, Connects "
               "cost, client id.",
    "get_client_history": "Read a CLIENT's public record by client id (cl_...): payment verified, spend, hires, "
                          "rating, past contracts. Use to judge a client, not a job.",
    "get_connects_balance": "My Connects balance, weekly allowance and today's submissions. Use for 'how many "
                            "Connects / can I afford / how many have I sent today'.",
    "get_market_stats": "Price percentiles (budgets, hourly rates) for a category. Use for 'what do jobs like this "
                        "pay / typical rate'. Not for finding individual jobs.",
    "list_proposals": "My proposals and their current status (draft, submitted, viewed, replied, hired, lost), "
                      "optionally filtered by status. Use for the state of my pipeline now.",
    "list_messages": "Read the conversation with the client on ONE proposal (proposal id prop_...).",
    "get_events": "What happened since a cursor: viewed, client message, declined, hired, closed, across all my "
                  "proposals. Use for 'anything new since ...'; for current status use list_proposals.",
    "create_proposal_draft": "Save a proposal as a DRAFT for a job (nothing is sent, no Connects). With dry_run=true "
                             "it only checks the draft and its Connects cost. Use before submitting.",
    "submit_proposal": "Submit an existing draft (prop_...) to the client: spends Connects. dry_run=true (default) "
                       "previews exactly what would be sent; dry_run=false only after explicit approval.",
    "send_message": "Send a message to the client in a proposal's conversation. dry_run=true (default) previews; "
                    "dry_run=false only after the user approved the exact text. Not for email.",
    "get_free_slots": "Find free times in the freelancer's calendar for a call/interview (working hours).",
    "create_tentative_hold": "Block a time on the freelancer's OWN calendar as tentative (no invite sent).",
    "list_holds": "List the tentative holds already on the freelancer's calendar.",
    "cancel_hold": "Remove one tentative hold by hold id.",
    "create_email_draft": "Save an EMAIL draft (outside the marketplace) for the freelancer to send themselves. "
                          "Never sends. Not for marketplace messages to clients.",
}


async def fine_specs() -> list[dict]:
    from mcp import Client

    from agent.mcp_client import _server_objects
    out = []
    for obj in _server_objects().values():
        async with Client(obj) as c:
            for t in (await c.list_tools()).tools:
                out.append({"type": "function", "function": {"name": t.name, "description": t.description or t.name,
                                                             "parameters": t.input_schema}})
    return out


def toolset(name: str, fine: list[dict]) -> list[dict]:
    if name == "fine":
        return fine
    if name == "best":
        return [{"type": "function", "function": {**t["function"], "description": BEST[t["function"]["name"]]}}
                for t in fine]
    params = {t["function"]["name"]: sorted(t["function"]["parameters"].get("properties", {})) for t in fine}
    out = []
    for server, actions in COARSE_ACTIONS.items():
        doc = "; ".join(f"{a}({', '.join(params[a])})" for a in actions)
        out.append({"type": "function", "function": {
            "name": server, "description": f"All {server} operations. Choose an action and pass its params. Actions: {doc}",
            "parameters": {"type": "object", "required": ["action"], "properties": {
                "action": {"type": "string", "enum": actions}, "params": {"type": "object"}}}}})
    return out


def normalise(set_name: str, name: str, args: dict) -> tuple[str, dict]:
    if set_name == "coarse":
        return args.get("action", f"{name}?"), args.get("params") or {}
    return name, args


def check_args(gold: dict, args: dict) -> tuple[int, int]:
    ok = 0
    for k, want in gold.items():
        got = args.get(k)
        if isinstance(want, dict) and "contains" in want:
            text = json.dumps(got).lower() if got is not None else ""
            ok += all(w.lower() in text for w in want["contains"])
        else:
            ok += got == want or (isinstance(want, str) and isinstance(got, str) and got.lower() == want.lower())
    return ok, len(gold)


def interleaved(tasks: list[dict]) -> list[dict]:
    """Round-robin over categories, so any prefix of the list covers every category."""
    by_cat: dict[str, list[dict]] = {}
    for t in tasks:
        by_cat.setdefault(t["category"], []).append(t)
    out, queues = [], list(by_cat.values())
    while any(queues):
        out += [q.pop(0) for q in queues if q]
    return out


async def run(a) -> None:
    from openai import BadRequestError

    from agent.llm import cost_usd, create_with_wait
    from agent.prompts import render

    tasks = interleaved(json.loads(TASKS_PATH.read_text())["tasks"])[: a.limit]
    fine = await fine_specs()
    tools_of = {s: toolset(s, fine) for s in a.sets}
    logs = {s: ResultLog(RESULTS / "runs" / f"tool_{s}.jsonl") for s in a.sets}
    spent = 0
    for t in tasks:                       # task by task across the sets: a run cut short stays a paired comparison
        if a.max_tokens and spent >= a.max_tokens:
            print(f"token cap reached ({spent:,} of {a.max_tokens:,}); stopping before task {t['id']}", flush=True)
            break
        for set_name in a.sets:
            tools, log = tools_of[set_name], logs[set_name]
            if log.done(t["id"]):
                continue
            t0 = time.perf_counter()
            system, user = render("eval_tool_study", {"request": t["request"]})
            effort = {"reasoning_effort": "low"} if a.model.startswith("openai/gpt-oss") else {}
            try:
                r = await asyncio.to_thread(create_with_wait, model=a.model, tool_choice="required", tools=tools,
                                            temperature=0, **effort,
                                            messages=[{"role": "system", "content": system},
                                                      {"role": "user", "content": user}])
            except BadRequestError as e:          # the provider rejects a call to a tool that does not exist
                if "tool call validation failed" not in str(e) and "did not call a tool" not in str(e):
                    raise
                bad = re.search(r"call tool '([^']*)'", str(e)) or re.search(r"(did not call a tool)", str(e))
                schema = re.search(r"parameters for tool (\w+) did not match schema", str(e))
                row = {"key": t["id"], "set": set_name, "model": a.model, "task": t["id"], "category": t["category"],
                       "gold_tool": t["tool"],
                       # right tool, arguments rejected by its schema: a correct selection with wrong arguments
                       "tool": schema.group(1) if schema else f"INVALID:{bad.group(1) if bad else '?'}", "args": {},
                       "tool_correct": bool(schema) and schema.group(1) == t["tool"], "args_ok": 0, "args_n": len(t["args"]), "args_all_correct": False,
                       "n_calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
                       "seconds": round(time.perf_counter() - t0, 2), "error": None, "rejected_by_api": True,
                       "rejection": re.sub(r"org_[A-Za-z0-9]+", "org_[redacted]", str(e))[:400]}
                log.add(row)
                print(f"{set_name:<6} {t['id']:<4} gold={t['tool']:<22} got={row['tool']} (rejected)", flush=True)
                continue
            calls = r.choices[0].message.tool_calls or []
            name, args = "", {}
            if calls:
                try:
                    args = json.loads(calls[0].function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                name, args = normalise(set_name, calls[0].function.name, args)
            ok, n = check_args(t["args"], args) if name == t["tool"] else (0, len(t["args"]))
            u = r.usage
            row = {"key": t["id"], "set": set_name, "model": a.model, "task": t["id"], "category": t["category"],
                   "gold_tool": t["tool"], "tool": name, "args": args, "tool_correct": name == t["tool"],
                   "args_ok": ok, "args_n": n, "args_all_correct": name == t["tool"] and ok == n,
                   "n_calls": len(calls), "tokens_in": u.prompt_tokens, "tokens_out": u.completion_tokens,
                   "cost_usd": cost_usd(a.model, u.prompt_tokens, u.completion_tokens),
                   "seconds": round(time.perf_counter() - t0, 2), "error": None}
            log.add(row)
            spent += u.prompt_tokens + u.completion_tokens
            print(f"{set_name:<6} {t['id']:<4} gold={t['tool']:<22} got={name:<22} args {ok}/{n} "
                  f"({u.prompt_tokens + u.completion_tokens} tok)", flush=True)
    summarise()


def summarise() -> dict:
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sets": {}}
    for set_name in ("fine", "coarse", "best"):
        p = RESULTS / "runs" / f"tool_{set_name}.jsonl"
        if not p.exists():
            continue
        rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        n = len(rows)
        cats: dict[str, list] = {}
        for r in rows:
            cats.setdefault(r["category"], []).append(r)
        out["sets"][set_name] = {
            "n_tasks": n, "model": rows[0]["model"] if rows else None,
            "tool_selection_accuracy": round(sum(r["tool_correct"] for r in rows) / n, 4),
            "argument_accuracy": round(sum(r["args_ok"] for r in rows) / max(1, sum(r["args_n"] for r in rows)), 4),
            "fully_correct_calls": round(sum(r["args_all_correct"] for r in rows) / n, 4),
            "tokens_per_task": round(sum(r["tokens_in"] + r["tokens_out"] for r in rows) / n, 1),
            "cost_per_task_usd": round(sum(r["cost_usd"] for r in rows) / n, 6),
            "by_category": {c: round(sum(r["tool_correct"] for r in rs) / len(rs), 3) for c, rs in sorted(cats.items())},
            "confusions": sorted({f"{r['gold_tool']} -> {r['tool'] or 'none'}" for r in rows if not r["tool_correct"]}),
        }
    (RESULTS / "tool_granularity.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ("by_category",)} for k, v in out["sets"].items()},
                     indent=1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    ap.add_argument("--sets", nargs="*", default=["fine", "coarse", "best"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-tokens", type=int, help="stop after spending this many tokens in this run")
    ap.add_argument("--summary-only", action="store_true")
    a = ap.parse_args()
    os.environ.update({"GIGBID_DB": "sqlite", "GIGBID_LANGFUSE": "off",
                       "GIGBID_RATE_WAIT": os.environ.get("GIGBID_RATE_WAIT", str(8 * 3600))})
    if a.summary_only:
        summarise()
        return
    asyncio.run(run(a))


if __name__ == "__main__":
    main()
