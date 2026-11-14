"""Prompt loader. One versioned Markdown file per node in this folder:

    ---
    version: 3
    ---
    ## system
    ...
    ## user
    ... {{job_post}} ...

`render(name, variables, schema)` fills {{placeholders}} and appends the JSON
schema the reply must follow. Prompts never live inline in code (CI checks it).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent
_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@lru_cache(maxsize=64)
def load(name: str) -> dict:
    raw = (DIR / f"{name}.md").read_text(encoding="utf-8")
    meta: dict = {}
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
        raw = raw[m.end():]
    parts = re.split(r"^## (system|user)\s*$", raw, flags=re.M)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if "system" not in sections or "user" not in sections:
        raise ValueError(f"prompt {name}.md needs '## system' and '## user' sections")
    return {"version": meta.get("version", "0"), **sections}


def render(name: str, variables: dict, schema: dict | None = None) -> tuple[str, str]:
    p = load(name)

    def fill(text: str) -> str:
        def rep(m: re.Match) -> str:
            if m.group(1) not in variables:
                raise KeyError(f"prompt {name}.md uses {{{{{m.group(1)}}}}} but it was not provided")
            v = variables[m.group(1)]
            return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)
        return _VAR.sub(rep, text)

    system = fill(p["system"])
    if schema is not None:
        system += ("\n\nReply with one JSON object that matches this JSON schema exactly, and nothing else:\n"
                   + json.dumps(schema, separators=(",", ":")))
    return system, fill(p["user"])


def version(name: str) -> str:
    return load(name)["version"]
