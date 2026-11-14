"""Render both graphs from code to Mermaid (docs/images/*.mmd), and print them.

    python -m agent.graphs.render
"""
from __future__ import annotations

from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.settings import ROOT


def main() -> None:
    out = ROOT / "docs/images"
    out.mkdir(parents=True, exist_ok=True)
    for name, g in [("bid_graph", build_bid_graph()), ("conversation_graph", build_conversation_graph())]:
        m = g.get_graph().draw_mermaid()
        (out / f"{name}.mmd").write_text(m)
        print(f"## {name}\n{m}")


if __name__ == "__main__":
    main()
