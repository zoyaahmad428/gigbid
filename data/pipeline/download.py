"""Download every Kaggle source into data/raw/<name>/ and record its licence.

Kaggle public datasets download without a token; the CLI prints the licence on
download, which we parse into reports/sources.json for the data card.

    python data/pipeline/download.py            # skip sources already present
    python data/pipeline/download.py --force    # re-download everything
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_config, write_json  # noqa: E402


def kaggle_bin() -> str:
    venv = Path(sys.executable).parent / "kaggle"
    return str(venv) if venv.exists() else (shutil.which("kaggle") or "kaggle")


def download(name: str, slug: str, raw: Path, force: bool) -> dict:
    dest = raw / name
    log = raw / f"{name}.log"
    have = dest.exists() and any(p.suffix in {".csv", ".xlsx"} for p in dest.iterdir())
    if force or not have or not log.exists():
        dest.mkdir(parents=True, exist_ok=True)
        with open(log, "w") as f:
            subprocess.run([kaggle_bin(), "datasets", "download", "-q", "-o", "--unzip",
                            "-p", str(dest), slug], stdout=f, stderr=subprocess.STDOUT, check=True)
    text = log.read_text(errors="replace")
    m = re.search(r"License\(s\):\s*(.+)", text)
    files = sorted(p.name for p in dest.iterdir() if not p.name.endswith(".kaggle-partial"))
    if any(p.name.endswith(".kaggle-partial") for p in dest.iterdir()):
        raise RuntimeError(f"{name}: partial download left in {dest}; rerun with --force")
    return {
        "name": name, "slug": slug,
        "url": f"https://www.kaggle.com/datasets/{slug}",
        "licence": m.group(1).strip() if m else "unverified",
        "files": files,
        "bytes": sum((dest / f).stat().st_size for f in files),
        "downloaded_at": dt.datetime.fromtimestamp(log.stat().st_mtime, dt.timezone.utc).isoformat(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    out = [download(n, s["slug"], cfg["paths"]["raw"], args.force) for n, s in cfg["sources"].items()]
    write_json(cfg["paths"]["reports"] / "sources.json", out)
    for s in out:
        print(f"{s['name']:28s} {s['licence']:32s} {s['bytes']/1e6:8.1f} MB")


if __name__ == "__main__":
    main()
