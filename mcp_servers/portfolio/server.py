"""Portfolio MCP server (read-only).

    python -m mcp_servers.portfolio.server                      # stdio, for Claude Desktop / Cursor
    python -m mcp_servers.portfolio.server --http --port 8201   # Streamable HTTP with bearer auth

stdio: one local user; PORTFOLIO_PERSONA picks the portfolio (default ai_ml).
HTTP:  PORTFOLIO_USERS="token=user_id:persona,..." maps bearer tokens to users.
PORTFOLIO_RETRIEVER picks the search variant (see rag/retriever.py).
"""
from __future__ import annotations

import argparse
import os

import mcp.types as t
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from mcp_servers.common import BearerAuth, current_user, local_user, parse_users
from mcp_servers.portfolio import prompts, resources
from mcp_servers.portfolio.tools import K, CaseStudy, Filters, Requirement, SearchResult, SkillList, store_for

READ = t.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
LOCAL = local_user("PORTFOLIO")


def _store():
    user = current_user(LOCAL)
    persona = user.persona or os.environ.get("PORTFOLIO_PERSONA", "ai_ml")
    try:
        return store_for(persona)
    except FileNotFoundError as e:
        raise ToolError(str(e)) from e


mcp = MCPServer(
    "gigbid-portfolio",
    title="GigBid Portfolio",
    version="1.0.0",
    instructions=("Read-only access to one freelancer's portfolio: CV, case studies, repo READMEs and code, "
                  "client reviews and past proposals with their outcomes. Use search_evidence to find proof "
                  "for a job requirement, get_case_study to read one in full, list_skills for an overview."),
)


@mcp.tool(title="Search portfolio evidence", annotations=READ)
def search_evidence(requirement: Requirement, k: K = 5, filters: Filters | None = None) -> SearchResult:
    """Find portfolio evidence for one job requirement. Returns the best items with type, source, date,
    outcome (for past proposals) and the matching passage. Search one requirement at a time."""
    return _store().search(requirement, k, filters)


@mcp.tool(title="Get a case study", annotations=READ)
def get_case_study(id: str) -> CaseStudy:
    """Read one case study in full, by its evidence id (from search_evidence) or its project id.
    Also lists the other documents about the same project (README, code, reviews)."""
    cs = _store().case_study(id)
    if cs is None:
        raise ToolError(f"No case study with id {id!r}. Use search_evidence to find ids.")
    return cs


@mcp.tool(title="List my skills", annotations=READ)
def list_skills() -> SkillList:
    """Every skill the portfolio shows, with how many projects and documents prove it, whether it is
    named or only implied, and when it was last used. Past proposals do not count as proof."""
    return _store().skills()


resources.register(mcp, _store)
prompts.register(mcp)


def http_app():
    users = parse_users(os.environ.get("PORTFOLIO_USERS", "dev-portfolio-ai=dev_ai:ai_ml"))
    return BearerAuth(mcp.streamable_http_app(), users)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8201)
    a = ap.parse_args()
    if a.http:
        import uvicorn
        uvicorn.run(http_app(), host=a.host, port=a.port)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
