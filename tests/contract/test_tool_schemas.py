"""Schema snapshot test: fails when an MCP tool's signature changes unintentionally.

Snapshots live in tests/contract/snapshots/<server>.json. After an intended change:
    UPDATE_SNAPSHOTS=1 pytest tests/contract
and commit the updated snapshot together with the code change.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from mcp import Client

SNAP = Path(__file__).parent / "snapshots"


def _servers():
    from mcp_servers.marketplace.server import mcp as marketplace
    from mcp_servers.portfolio.server import mcp as portfolio
    from mcp_servers.workspace.server import mcp as workspace
    return {"portfolio": portfolio, "marketplace": marketplace, "workspace": workspace}


async def _surface(server) -> dict:
    async with Client(server) as c:
        tools = (await c.list_tools()).tools
        templates = (await c.list_resource_templates()).resource_templates
        resources = (await c.list_resources()).resources
        prompts = (await c.list_prompts()).prompts
    return {
        "tools": {t.name: {"input_schema": t.input_schema, "output_schema": t.output_schema,
                           "annotations": t.annotations.model_dump(by_alias=True, exclude_none=True)
                           if t.annotations else None} for t in sorted(tools, key=lambda x: x.name)},
        "resource_templates": sorted(t.uri_template for t in templates),
        "resources": sorted(str(r.uri) for r in resources),
        "prompts": {p.name: sorted(a.name for a in (p.arguments or [])) for p in sorted(prompts, key=lambda x: x.name)},
    }


@pytest.mark.parametrize("name", ["portfolio", "marketplace", "workspace"])
async def test_tool_surface_matches_snapshot(name):
    current = await _surface(_servers()[name])
    path = SNAP / f"{name}.json"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        if os.environ.get("UPDATE_SNAPSHOTS") != "1":
            pytest.fail(f"created missing snapshot {path.name}; review and commit it")
    expected = json.loads(path.read_text())
    assert current == expected, f"{name} tool surface changed; rerun with UPDATE_SNAPSHOTS=1 if intended"
