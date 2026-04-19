from agents import COORDINATOR_PROMPT, CREATIVE_DIRECTOR_PROMPT, MEDIA_BUYER_PROMPT, build_agents
from tools.mcp_server import (
    CRITIQUE_RENDER_TOOL,
    RENDER_CREATIVE_TOOL,
    VIEW_BRAND_REFERENCE_TOOL,
)


def test_both_subagents_defined():
    agents = build_agents()
    assert set(agents.keys()) == {"creative-director", "media-buyer"}


def test_creative_director_has_render_view_and_critique_tools():
    agents = build_agents()
    creative_director = agents["creative-director"]
    # critique_render gives the creative-director a vision-based judgment
    # loop that goes through the raw Anthropic API, bypassing the SDK's
    # text-only MCP transport.
    assert creative_director.tools == [
        RENDER_CREATIVE_TOOL,
        VIEW_BRAND_REFERENCE_TOOL,
        CRITIQUE_RENDER_TOOL,
    ]
    assert creative_director.mcpServers == ["adpipeline"]
    assert creative_director.permissionMode == "bypassPermissions"


def test_media_buyer_is_scoped_to_pipeboard():
    agents = build_agents()
    media_buyer = agents["media-buyer"]
    assert media_buyer.tools is None
    assert media_buyer.mcpServers == ["pipeboard"]
    assert media_buyer.permissionMode == "bypassPermissions"


def test_coordinator_prompt_mentions_variant_budget_and_agentic_delegate_rules():
    prompt = COORDINATOR_PROMPT
    assert "variant_count" in prompt
    assert "budget_override" in prompt
    assert "PAUSED" in prompt
    assert "go live" in prompt
    # Whitespace-insensitive - the rule text can be reflowed across lines.
    flat = " ".join(prompt.split())
    assert "Do not bake adset targeting, objective, optimization goal, or budget" in flat


def test_coordinator_prompt_defers_meta_id_lookup_to_media_buyer():
    prompt = COORDINATOR_PROMPT
    flat = " ".join(prompt.split())
    # Coordinator must no longer demand ad_account_id / page_id from the user.
    assert "Do not stop to ask the user for Meta IDs" in flat
    assert "media-buyer will handle discovery" in flat


def test_creative_director_prompt_mentions_brand_reference_inputs():
    prompt = CREATIVE_DIRECTOR_PROMPT
    flat = " ".join(prompt.split())
    # view_brand_reference is explicitly disallowed on brand asset URLs now
    # (they're CDN-signed / flaky and clutter the UI with errors).
    assert "Do NOT call view_brand_reference" in flat
    assert "primary_color_hexes" in prompt
    assert "creative_copy_idea" in prompt


def test_creative_director_prompt_includes_design_principles():
    flat = " ".join(CREATIVE_DIRECTOR_PROMPT.split())
    assert "Hierarchy rule" in flat
    assert "Typography pairing" in flat
    assert "60-30-10" in flat
    assert "CTA button" in flat


def test_media_buyer_prompt_mentions_objective_budget_and_summary_fields():
    prompt = MEDIA_BUYER_PROMPT
    for objective in (
        "OUTCOME_TRAFFIC",
        "OUTCOME_SALES",
        "OUTCOME_LEADS",
        "OUTCOME_AWARENESS",
        "OUTCOME_ENGAGEMENT",
    ):
        assert objective in prompt
    assert "$5-$50/day" in prompt
    assert "budget_cap_applied" in prompt
    assert "targeting_summary" in prompt
    assert "status" in prompt
