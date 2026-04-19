"""Offline smoke: agents.build_agents and COORDINATOR_PROMPT are consistent.

We can't easily replay the full claude-agent-sdk loop without a real API key,
so we validate structural invariants that matter for correctness:
  - Both subagents exist with the expected keys.
  - creative-director has render_creative in its tools list.
  - media-buyer has pipeboard in its mcpServers list.
  - Coordinator prompt references both subagents by name and mentions scrape_url.
"""

import pytest

from agents import COORDINATOR_PROMPT, build_agents
from tools.mcp_server import RENDER_CREATIVE_TOOL


def test_both_subagents_defined():
    agents = build_agents()
    assert set(agents.keys()) == {"creative-director", "media-buyer"}


def test_creative_director_has_only_render_tool():
    agents = build_agents()
    cd = agents["creative-director"]
    assert cd.tools == [RENDER_CREATIVE_TOOL]
    assert cd.mcpServers in (None, [])


def test_media_buyer_scoped_to_pipeboard():
    agents = build_agents()
    mb = agents["media-buyer"]
    assert mb.mcpServers == ["pipeboard"]
    # tools=None means inherit — we want that so pipeboard MCP tools are usable
    assert mb.tools is None


def test_coordinator_prompt_mentions_key_roles():
    p = COORDINATOR_PROMPT
    assert "scrape_url" in p
    assert "creative-director" in p
    assert "media-buyer" in p
    assert "PAUSED" in p  # safety default must be in prompt
