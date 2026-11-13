"""Shared helpers: config loading, text cleaning, PII masking, stable ids, IO."""
from __future__ import annotations

import hashlib
import html
import json
import random
import re
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parents[2]          # repo root (gigbid/)
PIPELINE_DIR = Path(__file__).resolve().parent


def load_config() -> dict:
    with open(PIPELINE_DIR / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["paths"] = {k: ROOT / v for k, v in cfg["paths"].items()}
    return cfg


def rng(cfg: dict, stream: str) -> random.Random:
    """Independent, reproducible RNG per generator, so changing one generator
    does not reshuffle the output of another."""
    h = int(hashlib.sha256(f"{cfg['seed']}:{stream}".encode()).hexdigest()[:16], 16)
    return random.Random(h)


def stable_id(prefix: str, *parts: object, n: int = 10) -> str:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:n]
    return f"{prefix}_{h}"


# ---------------------------------------------------------------- cleaning
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def clean_text(s: object) -> str:
    if s is None or (isinstance(s, float) and s != s):   # None / NaN
        return ""
    t = html.unescape(str(s))
    t = t.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    t = _TAG_RE.sub(" ", t)
    t = t.replace(" ", " ").replace("​", "")
    t = _WS_RE.sub(" ", t)
    t = "\n".join(line.strip() for line in t.split("\n"))
    return _NL_RE.sub("\n\n", t).strip()


# ---------------------------------------------------------------- PII masking
# Applied to every real text field before it leaves data/raw/. Synthetic scam
# posts deliberately contain *fake* contact handles; those are generated after
# masking and use reserved example domains / numbers (see synth/scams.py).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
_HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{3,}")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")


def mask_pii(text: str) -> tuple[str, int]:
    """Mask emails, URLs, phone numbers, card/IBAN numbers and @handles.
    Returns (masked_text, number_of_replacements)."""
    n = 0

    def sub(pattern: re.Pattern, repl: str, t: str) -> str:
        nonlocal n
        t2, k = pattern.subn(repl, t)
        n += k
        return t2

    t = sub(_EMAIL_RE, "[EMAIL]", text)
    t = sub(_URL_RE, "[URL]", t)
    t = sub(_IBAN_RE, "[IBAN]", t)
    t = sub(_CARD_RE, "[CARD]", t)
    t = sub(_PHONE_RE, "[PHONE]", t)
    t = sub(_HANDLE_RE, "[HANDLE]", t)
    return t, n


# ---------------------------------------------------------------- IO
def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    k = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            k += 1
    return k


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
