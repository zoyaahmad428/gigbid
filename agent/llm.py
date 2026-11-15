"""LLM calls with structured output.

`structured(node, prompt_vars, Model)` renders the node's prompt from
agent/prompts/<node>.md, calls the node's model from config, asks for JSON,
validates it against the Pydantic model and retries once with the validation
error. On repeated failure of a cheap node it switches to the profile's
fallback model. Every call is written to the trace store with tokens and cost.
"""
from __future__ import annotations

import json
from collections import deque
import os
import re
import time
from functools import lru_cache
from typing import TypeVar

from openai import APIConnectionError, APITimeoutError, BadRequestError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from agent.prompts import render
from agent.settings import config, model_for, price, profile, provider
from agent.trace import TRACES

T = TypeVar("T", bound=BaseModel)
CHEAP_NODES = {"analyst", "risk_assessor", "compliance", "liaison_intent"}


class LLMError(RuntimeError):
    pass


def _root_cause(e: BaseException) -> str:
    """The innermost cause of a connection error (DNS, TLS, proxy, refused...), which the OpenAI
    client hides behind a bare "Connection error."."""
    while e.__cause__ is not None or e.__context__ is not None:
        e = e.__cause__ or e.__context__
    return f" <- {type(e).__name__}: {str(e)[:200]}"


@lru_cache(maxsize=4)
def _client(base_url: str, key_env: str) -> OpenAI:
    # strip: a key pasted into a hosting secret often ends in a newline, which httpx rejects as a
    # header value, and the OpenAI client reports that as a bare "APIConnectionError: Connection error."
    key = (os.environ.get(key_env) or "").strip()
    if not key:
        raise LLMError(f"{key_env} is not set (put the provider key in .env)")
    return OpenAI(api_key=key, base_url=base_url, timeout=config()["models"]["request_timeout_seconds"],
                  max_retries=2)


def client() -> OpenAI:
    p = provider()
    return _client(p["base_url"], p["api_key_env"])


def cost_usd(model: str, tin: int, tout: int) -> float:
    pi, po = price(model)
    return round((tin * pi + tout * po) / 1_000_000, 8)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


_WAIT_RX = re.compile(r"try again in ((?:[\d.]+(?:ms|h|m|s))+)", re.I)
_UNIT = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def rate_limit_wait(err: Exception) -> float | None:
    """Seconds the provider asks us to wait, from a 429 message ("Please try again in 7m12.5s", "577ms")."""
    m = _WAIT_RX.search(str(err))
    if not m:
        return None
    return round(sum(float(n) * _UNIT[u.lower()] for n, u in re.findall(r"([\d.]+)(ms|h|m|s)", m.group(1), re.I)), 3)


RATE_WAITS: deque[tuple[float, float]] = deque(maxlen=1000)   # (start, end) of recent rate-limit pauses


def rate_waited(t0: float, t1: float) -> float:
    """Seconds between t0 and t1 spent paused on rate limits (overlapping parallel waits count once)."""
    spans = sorted((max(a, t0), min(b, t1)) for a, b in RATE_WAITS if b > t0 and a < t1)
    total, end = 0.0, t0
    for a, b in spans:
        if b > end:
            total += b - max(a, end)
            end = b
    return round(total, 2)


def note_rate_wait(seconds: float) -> None:
    """Sleep out a rate limit and record the pause (evaluations report latency without it)."""
    start = time.perf_counter()
    time.sleep(seconds)
    RATE_WAITS.append((start, time.perf_counter()))


def create_with_wait(**kwargs):
    """chat.completions.create that waits out rate limits for up to GIGBID_RATE_WAIT seconds in total.

    The default (60 s) covers the per-minute limit in production. Evaluations set it to hours, so a
    run that hits the free tier's daily token limit pauses instead of failing or falling back to
    another model (which would change what is being measured)."""
    budget = float(os.environ.get("GIGBID_RATE_WAIT", "60"))
    while True:
        try:
            return client().chat.completions.create(**kwargs)
        except RateLimitError as e:
            wait = rate_limit_wait(e)
            if wait is None or wait + 1 > budget:
                raise
            if wait > 30:
                m = re.search(r"on (tokens|requests) per \w+.*?Limit \d+, Used \d+, Requested \d+", str(e))
                print(f"[rate limit] {kwargs.get('model')}: waiting {wait / 60:.1f} min "
                      f"({m.group(0) if m else str(e)[:200]})", flush=True)
            note_rate_wait(wait + 1)
            budget -= wait + 1


def chat(node: str, system: str, user: str, *, model: str | None = None, json_mode: bool = True,
         temperature: float | None = None) -> tuple[str, dict]:
    """One completion. Returns (text, usage). Traced."""
    model = model or model_for(node)
    temps = config()["models"]["temperature"]
    t = temps.get(node, temps["default"]) if temperature is None else temperature
    start = time.perf_counter()
    try:
        r = create_with_wait(
            model=model, temperature=t, messages=[{"role": "system", "content": system},
                                                  {"role": "user", "content": user}],
            **({"response_format": {"type": "json_object"}} if json_mode else {}))
    except Exception as e:
        TRACES.add("llm", model, input={"node": node, "user": user[:2000]}, output={"error": str(e)[:300]}, ok=False,
                   ms=(time.perf_counter() - start) * 1000)
        raise
    u = r.usage
    tin, tout = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
    usage = {"model": model, "tokens_in": tin, "tokens_out": tout, "cost_usd": cost_usd(model, tin, tout),
             "ms": round((time.perf_counter() - start) * 1000, 1)}
    text = r.choices[0].message.content or ""
    TRACES.add("llm", model, input={"node": node, "system": system[:1500], "user": user[:4000]}, output=text[:4000],
               ms=usage["ms"], tokens_in=tin, tokens_out=tout, cost_usd=usage["cost_usd"])
    return text, usage


def structured(node: str, out_model: type[T], variables: dict, *, prompt: str | None = None) -> tuple[T, dict]:
    """Prompt -> validated Pydantic object. Returns (object, usage summed over attempts)."""
    system, user = render(prompt or node, variables, schema=out_model.model_json_schema())
    models = [model_for(node)]
    if node in CHEAP_NODES and profile().get("fallback") and os.environ.get("GIGBID_NO_FALLBACK") != "1":
        models.append(profile()["fallback"])
    total = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0}
    last_err = ""
    for model in models:
        msg = user
        for _attempt in range(2):
            try:
                text, u = chat(node, system, msg, model=model)
            except BadRequestError as e:
                if "failed to generate json" not in str(e).lower():
                    raise
                # Groq's JSON mode sometimes fails to sample valid JSON (HTTP 400 "Failed to generate
                # JSON"). It is the same failure as an invalid reply: retry, then the fallback model.
                # (Found in the step-7 evaluation: it dead-lettered a job at the Analyst.)
                last_err = "provider failed to generate JSON"
                msg = render("json_retry", {"user": user})[1]
                continue
            except (APIConnectionError, APITimeoutError, RateLimitError) as e:
                last_err = f"{type(e).__name__}: {e}{_root_cause(e)}"
                break                                   # try the fallback model
            for k in ("tokens_in", "tokens_out", "cost_usd"):
                total[k] += u[k]
            total["calls"] += 1
            total["model"] = model
            try:
                return out_model.model_validate(_extract_json(text)), total
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = str(e)[:600]
                msg = (f"{user}\n\nYour previous reply was not valid. Error:\n{last_err}\n"
                       "Reply again with only the corrected JSON object.")
    raise LLMError(f"{node}: no valid output after retries ({last_err})")


def injection_probability(text: str) -> float | None:
    """Probability that text contains a prompt-injection attack (Llama Prompt Guard 2), or None
    if the classifier is not configured or unavailable. One signal among several in the Risk Assessor."""
    model = profile().get("injection_classifier")
    if not model:
        return None
    probs = []
    try:
        for i in range(0, min(len(text), 6000), 1500):          # the classifier has a short context
            chunk = text[i:i + 1600]
            start = time.perf_counter()
            r = client().chat.completions.create(model=model, messages=[{"role": "user", "content": chunk}])
            v = float((r.choices[0].message.content or "0").strip())
            u = r.usage
            TRACES.add("llm", model, input={"node": "injection_classifier", "chunk": chunk[:300]}, output=v,
                       ms=(time.perf_counter() - start) * 1000, tokens_in=u.prompt_tokens if u else 0,
                       cost_usd=cost_usd(model, u.prompt_tokens if u else 0, 0))
            probs.append(v)
    except Exception as e:  # noqa: BLE001  (a missing signal must not stop the job)
        TRACES.add("llm", model, input={"node": "injection_classifier"}, output={"error": str(e)[:200]}, ok=False)
        return None
    return max(probs) if probs else None
