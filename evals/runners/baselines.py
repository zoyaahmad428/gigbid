"""R&D question C baselines: (a) one ReAct agent holding all tools, (b) a prebuilt supervisor.

    python -m evals.runners.baselines --system react
    python -m evals.runners.baselines --system supervisor

Both run on the 9 jobs of the `c` set (one clean bid, one injected bid and one scam per
persona) with gpt-oss-20b, the model of every node of our graph under the `open_20b` profile; the same MCP tools through the same toolbox (read-only:
no tool can send or spend Connects), the marketplace policy and the user's settings in the
prompt, and the same output contract: a JSON object with the decision, price and proposal. Each
system has its own GigSim (evals/runners/common.py). Our graph's run of the same jobs is
graph_runs.py --set c --profile open_20b --fork-from decisions --tag c_graph.

Writes evals/results/runs/c_<system>.jsonl.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time

import httpx

from evals.runners.common import RESULTS, WORK, ResultLog, eval_gigsim, gold_by_id, job_set, user_for

MODEL = os.environ.get("C_MODEL", "openai/gpt-oss-20b")
TOOLS = {"get_job", "get_client_history", "get_market_stats", "get_connects_balance", "search_evidence",
         "get_evidence", "list_skills"}
GROUPS = {"researcher": {"get_job", "get_client_history", "get_market_stats", "get_connects_balance"},
          "evidence_finder": {"search_evidence", "get_evidence", "list_skills"}}

def brief(user: dict, job_id: str) -> str:
    from agent.prompts import render
    from agent.settings import ROOT
    system, _ = render("eval_baseline_agent", {
        "name": user["name"], "headline": user["headline"], "hourly_rate": str(user["hourly_rate"]),
        "min_fixed_budget": str(user["min_fixed_budget"]), "job_id": job_id,
        "policy": (ROOT / "data/policy_pack/marketplace_policy.md").read_text()})
    return system


def c_jobs() -> list[dict]:
    """Question C's jobs: per persona one clean bid, one injected bid and one scam (9; the free tier's budget)."""
    return job_set("c")


def parse(text: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    for m in reversed(list(re.finditer(r"\{.*\}", text, re.S))):
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
    return None


class WaitOn429(httpx.AsyncBaseTransport):
    """httpx transport that waits out a rate limit at the model call itself.

    Without it the OpenAI client gives up on the free tier's long daily-limit waits and the whole agent
    run would start again from the first message, spending the tokens twice. Pauses are recorded so
    latency can be reported without them (agent/llm.py: rate_waited)."""

    def __init__(self, max_wait: float = 8 * 3600):
        self.inner = httpx.AsyncHTTPTransport()
        self.max_wait = max_wait

    async def handle_async_request(self, request):
        from agent.llm import RATE_WAITS, rate_limit_wait
        while True:
            resp = await self.inner.handle_async_request(request)
            if resp.status_code != 429:
                return resp
            body = await resp.aread()
            await resp.aclose()
            wait = rate_limit_wait(body.decode(errors="replace"))
            if wait is None or wait > self.max_wait:
                return httpx.Response(429, headers=resp.headers, content=body, request=request)
            if wait > 30:
                print(f"  rate limited; waiting {wait / 60:.1f} min", flush=True)
            start = time.perf_counter()
            await asyncio.sleep(wait + 1)
            RATE_WAITS.append((start, time.perf_counter()))

    async def aclose(self):
        await self.inner.aclose()


def chat_model():
    from langchain_openai import ChatOpenAI

    from agent.settings import provider
    p = provider()
    return ChatOpenAI(model=MODEL, base_url=p["base_url"], api_key=os.environ[p["api_key_env"]], temperature=0.2,
                      max_retries=2, timeout=120,
                      http_async_client=httpx.AsyncClient(transport=WaitOn429(), timeout=120))


CONTEXT_TOKENS = 4500          # the free tier rejects requests over ~7k input tokens; keep each call under it
TOOL_CHARS = 1500


def trim_hook(state: dict) -> dict:
    """Standard context management: keep the system prompt, the task and the most recent messages."""
    from langchain_core.messages import trim_messages
    msgs = state["messages"]
    head = msgs[:1]
    tail = trim_messages(msgs[1:], max_tokens=CONTEXT_TOKENS, token_counter="approximate", strategy="last",
                         start_on=("ai", "human"), allow_partial=False)
    return {"llm_input_messages": head + tail}


async def tools_for(agent: str, names: set[str], toolbox) -> list:
    """The MCP tools through our toolbox, with evidence searches capped at k=3 and long outputs cut."""
    from langchain_core.tools import StructuredTool

    from agent import mcp_client
    mcp_client.ALLOWLIST[agent] = names
    out = []
    for t in await mcp_client.langchain_tools(agent, toolbox):
        async def run(_t=t, **kw):
            if _t.name == "search_evidence":
                kw["k"] = min(int(kw.get("k") or 3), 3)
            try:
                res = json.dumps(await _t.coroutine(**kw), default=str)
            except Exception as e:                       # the agent sees the error and can recover
                return f"ERROR: {str(e)[:300]}"
            return res if len(res) <= TOOL_CHARS else res[:TOOL_CHARS] + "...[truncated]"
        out.append(StructuredTool.from_function(coroutine=run, name=t.name, description=t.description,
                                                args_schema=t.args_schema))
    return out


async def build(system: str, toolbox, system_prompt: str):
    from langgraph.prebuilt import create_react_agent
    model = chat_model()
    if system == "react":
        return create_react_agent(model, await tools_for("eval_single_agent", TOOLS, toolbox), name="single_agent",
                                  prompt=system_prompt, pre_model_hook=trim_hook)
    from langgraph_supervisor import create_supervisor

    from agent.prompts import render
    workers = []
    for name, names in GROUPS.items():
        workers.append(create_react_agent(model, await tools_for(f"eval_{name}", names, toolbox), name=name,
                                          pre_model_hook=trim_hook,
                                          prompt=render("eval_baseline_worker", {"role": name.replace("_", " ")})[0]))
    workers.append(create_react_agent(model, [], name="writer", pre_model_hook=trim_hook,
                                      prompt=render("eval_baseline_writer", {})[0]))
    return create_supervisor(workers, model=model, pre_model_hook=trim_hook,
                             prompt=system_prompt + "\n" + render("eval_baseline_supervisor", {})[0],
                             output_mode="last_message").compile()


def usage_of(cb) -> dict:
    """Tokens of every model call in the job, including attempts that failed (callback totals)."""
    from agent.llm import cost_usd
    out = {}
    for model, u in (cb.usage_metadata or {}).items():
        name = model if "/" in model else MODEL
        tin, tout = u.get("input_tokens", 0), u.get("output_tokens", 0)
        out[name] = {"tokens_in": tin, "tokens_out": tout, "cost_usd": cost_usd(name, tin, tout)}
    return out or {MODEL: {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}}


async def run(a) -> None:
    from langchain_core.callbacks import UsageMetadataCallbackHandler
    from openai import BadRequestError, RateLimitError

    from agent.llm import rate_limit_wait, rate_waited
    from agent.mcp_client import Toolbox
    from agent.trace import bind_thread
    from mcp_servers.common import User, bind

    gold = gold_by_id()
    log = ResultLog(RESULTS / "runs" / f"c_{a.system}.jsonl")
    toolbox = Toolbox()
    jobs = c_jobs()[: a.limit]
    for i, job in enumerate(jobs):
        if log.done(job["job_id"]):
            continue
        user = user_for(job["persona"])
        tid = f"c_{a.system}:{job['job_id']}"
        bind(User(user_id=user["user_id"], persona=user["persona"], marketplace_token=user["marketplace_token"]),
             trace=tid.replace(":", "-"))
        bind_thread(tid)
        g = gold[job["job_id"]]
        agent = await build(a.system, toolbox, brief(user, job["job_id"]))
        cb = UsageMetadataCallbackHandler()
        t0 = time.perf_counter()
        bad_calls, failures = 0, []
        for _attempt in range(8):
            err, msgs = None, []
            try:
                out = await agent.ainvoke({"messages": [{"role": "user", "content": f"Job id: {job['job_id']}"}]},
                                          {"recursion_limit": 40, "callbacks": [cb]})
                msgs = out["messages"]
                break
            except RateLimitError as e:
                wait = rate_limit_wait(e) or 60
                print(f"  rate limited; waiting {wait / 60:.1f} min", flush=True)
                await asyncio.sleep(wait + 2)
            except BadRequestError as e:                 # e.g. a call to a misspelled tool, rejected by the API
                err = f"{type(e).__name__}: {str(e)[:300]}"
                failures.append(err)
                bad_calls += 1
                if bad_calls > 2:                        # the job is retried twice, then counted as failed
                    break
            except Exception as e:                       # recursion limit, tool errors: a failed run is a result
                err = f"{type(e).__name__}: {str(e)[:300]}"
                failures.append(err)
                break
        final = parse(msgs[-1].content if msgs else "") or {}
        tool_calls = [tc["name"] for m in msgs for tc in (getattr(m, "tool_calls", None) or [])]
        row = {"key": job["job_id"], "job_id": job["job_id"], "persona": job["persona"], "kind": job["kind"],
               "system": a.system, "gold_action": g["gold_action"], "is_scam": g["is_scam"],
               "has_injection": g["has_injection"], "fair_price_band": g.get("fair_price_band"),
               "decision": final.get("decision"), "decision_reasons": final.get("reasons"),
               "price": final.get("bid_amount"), "bid_type": final.get("bid_type"),
               "draft": final.get("cover_letter"), "answers": final.get("screening_answers"),
               "parsed": bool(final), "tool_calls": tool_calls, "n_messages": len(msgs),
               "seconds": round(time.perf_counter() - t0, 2), "tokens": usage_of(cb), "failed_attempts": failures,
               "seconds_net": round(time.perf_counter() - t0 - rate_waited(t0, time.perf_counter()), 2),
               "error": err}
        log.add(row)
        spent = sum(v["tokens_in"] + v["tokens_out"] for v in row["tokens"].values())
        print(f"[{i + 1}/{len(jobs)}] {job['job_id']} {job['kind']:<12} -> {row['decision']} parsed={row['parsed']} "
              f"tools={len(tool_calls)} {row['seconds']:.0f}s {spent} tok {err or ''}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", required=True, choices=["react", "supervisor"])
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    tag = f"c_{a.system}"
    os.environ.update({"GIGBID_DB": "sqlite", "GIGBID_TRACE_DB": str(WORK / f"traces_{tag}.sqlite3"),
                       "GIGBID_LANGFUSE": "off"})
    with eval_gigsim(tag) as url:
        os.environ["GIGSIM_BASE_URL"] = url
        asyncio.run(run(a))


if __name__ == "__main__":
    main()
