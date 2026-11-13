"""Resource templates so a host (Claude Desktop, Cursor, GigBid) can load full items."""
from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError


def register(mcp: MCPServer, store) -> None:
    @mcp.resource("portfolio://case/{case_id}", mime_type="text/markdown",
                  description="A case study as Markdown (case-study evidence id or project id)")
    def case(case_id: str) -> str:
        cs = store().case_study(case_id)
        if cs is None:
            raise ResourceError(f"no case study {case_id}")
        return cs.markdown

    @mcp.resource("portfolio://proposal/{proposal_id}", mime_type="application/json",
                  description="A past proposal with its outcome (won, replied or ignored)")
    def proposal(proposal_id: str) -> str:
        d = store().by_id.get(proposal_id)
        if d is None or d.type != "proposal":
            raise ResourceError(f"no proposal {proposal_id}")
        return json.dumps({**d.meta(), "text": d.text})

    @mcp.resource("portfolio://doc/{evidence_id}", mime_type="text/plain",
                  description="Any portfolio document (README, code, review, ...) by evidence id")
    def doc(evidence_id: str) -> str:
        d = store().by_id.get(evidence_id)
        if d is None:
            raise ResourceError(f"no document {evidence_id}")
        return d.text

    @mcp.resource("portfolio://cv", mime_type="text/markdown", description="The freelancer's CV")
    def cv() -> str:
        d = next((x for x in store().docs if x.type == "cv"), None)
        if d is None:
            raise ResourceError("no CV in this portfolio")
        return d.text
