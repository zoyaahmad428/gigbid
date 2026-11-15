"""Launcher for a Hugging Face Space on the Gradio SDK (free CPU), copied to the Space as app.py.

The Space runs `python app.py` after installing requirements.txt. This doesn't use Gradio: it starts
the same single-container stack as the Docker image (infra/docker/start-all.sh: GigSim, then the
API, which serves the built web app on port 7860). There is no redis-server on this SDK, so runs
execute inside the API process (GIGBID_RUNNER=inprocess) and no worker is started.

The retriever's models are downloaded and warmed before the API starts, so the first analysis
doesn't wait for 200 MB of downloads. The Space shows "Starting" until then.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
os.environ.setdefault("GIGBID_RUNNER", "inprocess")
os.environ.setdefault("GIGBID_DB", "auto")
os.environ.setdefault("PORT", "7860")
os.environ["PYTHONPATH"] = f"{ROOT}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"
sys.path.insert(0, str(ROOT))

from rag.models import _embedder, _reranker, embedding_name, reranker_name  # noqa: E402

list(_embedder(embedding_name()).embed(["warm"]))
list(_reranker(reranker_name()).rerank("q", ["p"]))
print("retriever models ready; starting GigSim and the API on port", os.environ["PORT"], flush=True)

os.execvp("bash", ["bash", str(ROOT / "infra/docker/start-all.sh")])
