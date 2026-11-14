"""Prompt templates a freelancer would actually pick in their MCP host.

The text lives in agent/prompts/mcp_portfolio_*.md, like every other prompt; the host supplies
its own system prompt, so only the user message is served.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from agent.prompts import render


def _user(name: str, **variables: str) -> str:
    return render(name, variables)[1]


def register(mcp: MCPServer) -> None:
    @mcp.prompt(title="Answer a screening question from my portfolio")
    def answer_screening_question(question: str, job_title: str = "") -> str:
        """Find evidence for a client's screening question and answer it in 2-4 sentences."""
        ctx = f' for the job "{job_title}"' if job_title else ""
        return _user("mcp_portfolio_screening_question", question=question, job_context=ctx)

    @mcp.prompt(title="Prove a requirement")
    def prove_requirement(requirement: str) -> str:
        """List the strongest proof that I meet one requirement, or say that I don't."""
        return _user("mcp_portfolio_prove_requirement", requirement=requirement)

    @mcp.prompt(title="Outline a proposal for a job post")
    def outline_proposal(job_post: str) -> str:
        """Break a job post into requirements and map each to portfolio evidence."""
        return _user("mcp_portfolio_outline_proposal", job_post=job_post)
