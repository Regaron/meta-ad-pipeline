from agents import COORDINATOR_PROMPT, CREATIVE_DIRECTOR_PROMPT, MEDIA_BUYER_PROMPT, build_agents
from tools.mcp_server import RENDER_CREATIVE_TOOL, VIEW_BRAND_REFERENCE_TOOL


def test_both_subagents_defined():
    agents = build_agents()
    assert set(agents.keys()) == {"creative-director", "media-buyer"}


def test_creative_director_has_render_and_view_tools():
    agents = build_agents()
    creative_director = agents["creative-director"]
    assert creative_director.tools == [RENDER_CREATIVE_TOOL, VIEW_BRAND_REFERENCE_TOOL]
    assert creative_director.mcpServers in (None, [])


def test_media_buyer_is_scoped_to_pipeboard():
    agents = build_agents()
    media_buyer = agents["media-buyer"]
    assert media_buyer.tools is None
    assert media_buyer.mcpServers == ["pipeboard"]


def test_coordinator_prompt_mentions_variant_budget_and_agentic_delegate_rules():
    prompt = COORDINATOR_PROMPT
    assert "variant_count" in prompt
    assert "budget_override" in prompt
    assert "PAUSED" in prompt
    assert "go live" in prompt
    assert "Do not bake adset targeting, objective, optimization goal, or budget" in prompt


def test_creative_director_prompt_mentions_brand_reference_inputs():
    prompt = CREATIVE_DIRECTOR_PROMPT
    assert "view_brand_reference" in prompt
    assert "primary_color_hexes" in prompt
    assert "creative_copy_idea.headline" in prompt
    assert "do NOT embed" in prompt


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
