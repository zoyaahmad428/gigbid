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

# mcp 2.x and pydantic 2.14 can't be in requirements.txt: the SDK installs it together with
# gradio[mcp], which pins mcp<2 and pydantic<=2.12.5 (see hf_space.sh). Install it here, before anything imports it. --user goes to ~/.local,
# which is writable at runtime and comes before the system site-packages on sys.path.
_runtime_reqs = ROOT / "requirements-runtime.txt"
if _runtime_reqs.exists() and _runtime_reqs.read_text().strip():
    import site
    import subprocess

    pip = [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
           "-q", "-r", str(_runtime_reqs)]
    if subprocess.run([*pip, "--user"]).returncode != 0:
        subprocess.run(pip, check=True)
    user_site = site.getusersitepackages()
    if user_site not in sys.path:
        sys.path.insert(1, user_site)
    print("runtime requirements installed:", _runtime_reqs.read_text().split(), flush=True)

# ZeroGPU hardware (the free tier on some accounts) stops the Space with "No @spaces.GPU function
# detected during startup" unless the app registers a @spaces.GPU function and sends ZeroGPU its
# startup report. The `spaces` package does that from gradio's launch(), which GigBid never calls,
# so register a placeholder and run the same hook here. GigBid needs no GPU: retrieval runs on
# ONNX on the CPU and the LLM calls go to Groq.
if os.environ.get("SPACES_ZERO_GPU", "").lower() in {"1", "true"}:
    import spaces  # must come before anything that could initialise CUDA
    import spaces.zero

    @spaces.GPU(duration=1)
    def _zerogpu_placeholder() -> None:
        """Never called; it only satisfies ZeroGPU's start-up check."""

    spaces.zero.startup()
    print("ZeroGPU startup report sent (GigBid runs on the CPU)", flush=True)

from rag.models import _embedder, _reranker, embedding_name, reranker_name  # noqa: E402

list(_embedder(embedding_name()).embed(["warm"]))
list(_reranker(reranker_name()).rerank("q", ["p"]))
print("retriever models ready; starting GigSim and the API on port", os.environ["PORT"], flush=True)

os.execvp("bash", ["bash", str(ROOT / "infra/docker/start-all.sh")])
