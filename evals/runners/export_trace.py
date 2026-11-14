"""Export one thread's execution trace to a Markdown page for the README (public trace links).

    python -m evals.runners.export_trace --db data/processed/traces.sqlite3 \
        --thread bid:u_5188b642aac8:job_03615f2880 --name approved-proposal --title "..."

Writes docs/traces/<name>.md: per-node tokens, cost and time, then every model and tool call in
order with its (already PII-masked) input and output. Provider account ids that appear in error
messages are redacted. Traces come from agent/trace.py's store; Langfuse holds the same events
when it is configured.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/traces"
REDACT = [(re.compile(r"org_[A-Za-z0-9]+"), "org_[redacted]"), (re.compile(r"Bearer\s+\S+"), "Bearer [redacted]")]


def clean(s: str | None, limit: int) -> str:
    s = s or ""
    for rx, rep in REDACT:
        s = rx.sub(rep, s)
    try:
        s = json.dumps(json.loads(s), indent=1, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        pass
    return s if len(s) <= limit else s[:limit] + f"\n… ({len(s) - limit} more characters)"


def export(db_path: str, thread: str, name: str, title: str, note: str = "", limit: int = 1500) -> Path:
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT seq, ts, node, kind, name, input, output, ok, ms, tokens_in, tokens_out, cost_usd "
                      "FROM events WHERE thread_id=? ORDER BY seq", (thread,)).fetchall()
    if not rows:
        raise SystemExit(f"no events for {thread} in {db_path}")
    t0 = rows[0][1]
    nodes: dict[str, dict] = {}
    for _, _, node, kind, _, _, _, _, ms, ti, to, cost in rows:
        n = nodes.setdefault(node or "-", {"llm": 0, "tool": 0, "error": 0, "tokens": 0, "cost": 0.0, "ms": 0.0})
        n[kind] = n.get(kind, 0) + 1
        n["tokens"] += (ti or 0) + (to or 0)
        n["cost"] += cost or 0
        n["ms"] += ms or 0
    total_tok = sum(n["tokens"] for n in nodes.values())
    total_cost = sum(n["cost"] for n in nodes.values())
    lines = [f"# Trace: {title}", "", f"Thread `{thread}`, {len(rows)} events, {total_tok:,} tokens, "
             f"${total_cost:.4f}, {rows[-1][1] - t0:.0f} s from first to last event.", ""]
    if note:
        lines += [note, ""]
    lines += ["Exported from GigBid's trace store with `python -m evals.runners.export_trace`. Inputs were "
              "PII-masked when recorded. Time includes any rate-limit pauses on the free API tier.", "",
              "## Per node", "", "| Node | Model calls | Tool calls | Errors | Tokens | Cost (USD) | Time (s) |",
              "|---|---|---|---|---|---|---|"]
    for node, n in nodes.items():
        lines.append(f"| {node} | {n['llm']} | {n['tool']} | {n['error']} | {n['tokens']:,} | {n['cost']:.5f} | "
                     f"{n['ms'] / 1000:.1f} |")
    lines += ["", "## Events", ""]
    for seq, ts, node, kind, nm, inp, outp, ok, ms, ti, to, cost in rows:
        mark = "" if ok else " **failed**"
        tok = f", {ti or 0}+{to or 0} tokens, ${cost or 0:.5f}" if kind == "llm" else ""
        lines += [f"<details><summary>+{ts - t0:.1f} s · <b>{node}</b> · {kind} <code>{nm}</code>{mark} "
                  f"({(ms or 0) / 1000:.1f} s{tok})</summary>", "", "Input:", "", "```", clean(inp, limit), "```", "",
                  "Output:", "", "```", clean(outp, limit), "```", "", "</details>", ""]
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.md"
    path.write_text("\n".join(lines))
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--thread", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    print(export(a.db, a.thread, a.name, a.title, a.note))


if __name__ == "__main__":
    main()
