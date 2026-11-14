"""Warm start for a demo deployment: move a *fresh* GigSim's clock forward.

GigSim's clock starts where its live job stream starts, so a new deployment has almost no open
jobs for its first minutes (about 35 jobs are posted per simulated day, and a simulated day is 24
real minutes). This moves the clock of a fresh simulator forward GIGSIM_WARM_START_HOURS (default
72, about 110 open jobs) through GigSim's admin clock endpoint. If the clock has already moved
that far (state kept from an earlier run), it does nothing.

    python infra/docker/gigsim_warm.py      (start-all.sh and the compose `gigsim-init` service)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def call(method: str, path: str, body: dict | None = None) -> dict:
    base = os.environ.get("GIGSIM_BASE_URL", "http://127.0.0.1:8081").rstrip("/")
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"x-admin-token": os.environ.get("GIGSIM_ADMIN_TOKEN", "dev-admin"),
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def parse(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def main() -> None:
    hours = float(os.environ.get("GIGSIM_WARM_START_HOURS", "72"))
    start = parse(yaml.safe_load((ROOT / "gigsim/config.yaml").read_text())["clock"]["start"])
    now = parse(call("GET", "/admin/clock")["sim_time"])
    elapsed = (now - start).total_seconds() / 3600
    if hours <= 0 or elapsed >= hours:
        print(f"GigSim clock at {now:%Y-%m-%d %H:%M} ({elapsed:.0f} h in); no warm start needed")
        return
    call("POST", "/admin/clock", {"advance_hours": round(hours - elapsed, 2)})
    print(f"GigSim clock moved forward {hours - elapsed:.0f} h to {call('GET', '/admin/clock')['sim_time'][:16]}")


if __name__ == "__main__":
    main()
