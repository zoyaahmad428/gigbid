"""agent/config.yaml plus environment, loaded once. Every node's model is set
here (config), never in code; rules and thresholds too, so a platform policy
change is a config edit."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((ROOT / "agent" / "config.yaml").read_text())


def profile_name() -> str:
    return os.environ.get("MODEL_PROFILE", config()["models"]["profile"])


def profile() -> dict:
    name = profile_name()
    profs = config()["models"]["profiles"]
    if name not in profs:
        raise ValueError(f"unknown model profile {name!r}; available: {sorted(profs)}")
    return profs[name]


def model_for(node: str) -> str:
    return profile()["nodes"][node]


def provider() -> dict:
    return config()["models"]["providers"][profile()["provider"]]


def price(model: str) -> tuple[float, float]:
    """USD per 1M tokens (input, output); (0, 0) if unknown."""
    return tuple(provider().get("prices", {}).get(model, (0.0, 0.0)))  # type: ignore[return-value]


def postgres_url() -> str | None:
    """The Postgres URL for checkpoints and the memory store, or None for local SQLite.
    GIGBID_DB=sqlite forces SQLite even when DATABASE_URL is set (the starter .env points at
    the docker-compose Postgres, which is not running in local dev)."""
    url = os.environ.get("DATABASE_URL", "")
    if os.environ.get("GIGBID_DB", "auto") == "sqlite" or not url.startswith("postgres"):
        return None
    return url.replace("+psycopg", "")
