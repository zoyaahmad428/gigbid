"""The repository rules the brief asks CI to enforce. Exit code 1 lists every violation.

    python infra/ci/check_repo_rules.py                 # all four checks
    python infra/ci/check_repo_rules.py --freeze-gigsim # rewrite infra/ci/gigsim.sha256 (only on purpose)

1. No secrets: no API keys or private keys in tracked files, and no tracked .env.
2. Nothing under gigsim/ changed: every tracked file matches infra/ci/gigsim.sha256. GigSim is
   the environment the system is graded in; policy changes go through GIGSIM_POLICY files.
3. Every prompt lives in agent/prompts/: no prompt-like string literal in Python code elsewhere.
4. Every number traces to evals/results/: each table in README.md or docs/REPORT.md that has
   decimal numbers names its results file just above it, and every such number appears in that
   file (to the precision shown) or is a declared setting in agent/config.yaml.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "infra/ci/gigsim.sha256"

SECRET_PATTERNS = {
    "Groq key": r"gsk_[A-Za-z0-9]{20,}",
    "OpenAI-style key": r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}",
    "Hugging Face token": r"\bhf_[A-Za-z0-9]{30,}",
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{36,}",
    "AWS access key": r"\bAKIA[0-9A-Z]{16}\b",
    "Langfuse secret key": r"\bsk-lf-[0-9a-f-]{20,}",
    "Slack token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
}
# Prompt-like literals: instructions addressed to a model.
PROMPT_RX = re.compile(r"\b(You are|you must|Reply with|Respond (?:with|only)|Do not answer|call exactly one|"
                       r"Never invent|Use (?:your|the) tools|Finish with ONLY|Cite (?:each|evidence))\b")
PROMPT_DIRS = ("agent", "apps", "worker", "evals", "mcp_servers", "rag", "infra")


def tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode().split("\0") if p and (ROOT / p).is_file()]   # skip deleted files


def check_secrets(files: list[Path]) -> list[str]:
    errs = [f"{f.relative_to(ROOT)} is tracked" for f in files if f.name == ".env"]
    rx = {k: re.compile(v) for k, v in SECRET_PATTERNS.items()}
    for f in files:
        if f.suffix in {".png", ".jpg", ".gif", ".pdf", ".onnx", ".parquet", ".sqlite3", ".ico", ".woff2"}:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except (IsADirectoryError, FileNotFoundError):
            continue
        for name, r in rx.items():
            if r.search(text):
                errs.append(f"{f.relative_to(ROOT)}: looks like a {name}")
    return errs


def gigsim_hashes(files: list[Path]) -> dict[str, str]:
    return {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(files) if f.relative_to(ROOT).parts[0] == "gigsim" and f.is_file()}


def check_gigsim(files: list[Path]) -> list[str]:
    if not MANIFEST.exists():
        return [f"{MANIFEST.relative_to(ROOT)} is missing (run with --freeze-gigsim once)"]
    want = dict(line.split("  ", 1)[::-1] for line in MANIFEST.read_text().splitlines() if line.strip())
    have = gigsim_hashes(files)
    errs = [f"{p} changed" for p in sorted(want.keys() & have.keys()) if want[p] != have[p]]
    errs += [f"{p} added" for p in sorted(have.keys() - want.keys())]
    errs += [f"{p} removed" for p in sorted(want.keys() - have.keys())]
    return errs


def _literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def check_prompts(files: list[Path]) -> list[str]:
    errs = []
    for f in files:
        rel = f.relative_to(ROOT)
        if f.suffix != ".py" or rel.parts[0] not in PROMPT_DIRS or rel.parts[:2] == ("agent", "prompts") \
                or "tests" in rel.parts or rel == Path("infra/ci/check_repo_rules.py"):
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                      and n.body and isinstance(n.body[0], ast.Expr)}
        for node in ast.walk(tree):
            if id(node) in docstrings:
                continue
            text = _literal_text(node)
            if text and len(text) >= 40 and PROMPT_RX.search(text):
                errs.append(f"{rel}:{node.lineno}: prompt text in code ({text[:60]!r}...); move it to agent/prompts/")
    return errs


NUM = re.compile(r"(?<![\w.])-?\d+\.\d{2,}(?![\w.])")
SRC = re.compile(r"[\w./-]+\.(?:json|md)\b")


def _values(path: Path) -> set[float]:
    return {float(n) for n in re.findall(r"-?\d+\.\d+|-?\d+", path.read_text(encoding="utf-8", errors="ignore"))}


def check_numbers() -> list[str]:
    """Each table with decimal numbers names its source just above it; every such number is in that source.

    A source is a file under evals/results/ named (by file name) in the six lines before the table,
    e.g. "(`evals/results/summary.json`)". Declared settings from agent/config.yaml are always allowed.
    """
    results = {p.name: p for p in (ROOT / "evals/results").glob("*") if p.suffix in {".json", ".md"}}
    settings = _values(ROOT / "agent/config.yaml")
    errs = []
    for doc in (ROOT / "README.md", ROOT / "docs/REPORT.md"):
        if not doc.exists():
            continue
        lines = doc.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            if not lines[i].lstrip().startswith("|"):
                i += 1
                continue
            start = i
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                i += 1
            nums = [(n, k + 1) for k in range(start, i) for n in NUM.findall(lines[k])]
            if not nums:
                continue
            context = " ".join(lines[max(0, start - 6):start] + lines[start:i])
            sources = [results[Path(m).name] for m in SRC.findall(context) if Path(m).name in results]
            if not sources and "config.yaml" not in context:
                errs.append(f"{doc.relative_to(ROOT)}:{start + 1}: table with numbers but no evals/results/ source named above it")
                continue
            known = set().union(*(_values(p) for p in sources)) | settings
            for n, line_no in nums:
                places = len(n.split(".")[1])
                if not any(round(k, places) == float(n) for k in known):
                    errs.append(f"{doc.relative_to(ROOT)}:{line_no}: {n} is not in "
                                f"{', '.join(p.name for p in sources)} or agent/config.yaml")
    return errs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze-gigsim", action="store_true")
    a = ap.parse_args()
    files = tracked()
    if a.freeze_gigsim:
        MANIFEST.write_text("".join(f"{h}  {p}\n" for p, h in gigsim_hashes(files).items()))
        print(f"wrote {MANIFEST.relative_to(ROOT)}")
        return
    failed = False
    for name, errs in (("no secrets", check_secrets(files)), ("gigsim/ unchanged", check_gigsim(files)),
                       ("prompts in agent/prompts/", check_prompts(files)),
                       ("numbers trace to evals/results/", check_numbers())):
        print(f"{'FAIL' if errs else 'ok  '}  {name}")
        for e in errs:
            print(f"      {e}")
        failed |= bool(errs)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
