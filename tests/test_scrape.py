from unittest.mock import AsyncMock, patch

import pytest

from tools.schemas import BrandResearch


_CANNED_RESEARCH = BrandResearch(
    source_url="https://acme.com",
    identity={
        "logo_url": "https://acme.com/logo.svg",
        "primary_color_hexes": ["#0F62FE", "#161616"],
    },
    value_prop={
        "headline": "Premium widgets engineered to last.",
        "top_3_benefits": [
            "Free shipping",
            "Lifetime warranty",
            "30-day returns",
        ],
    },
    visual_asset_urls=[
        "https://acme.com/img/hero.jpg",
        "https://acme.com/img/lifestyle.jpg",
    ],
    tone_adjectives=["confident", "warm", "playful"],
    cta_button_text="Get Started",
    creative_copy_idea={
        "hook": "Tired of widgets that break?",
        "body": "Our widgets are built from aerospace-grade materials.",
        "headline": "Widgets That Outlast You",
    },
)


@pytest.mark.asyncio
async def test_scrape_url_returns_text_block_with_brand_research_json():
    from tools.scrape import _scrape_handler

    fake_result = AsyncMock()
    fake_result.output = _CANNED_RESEARCH

    fake_client = AsyncMock()
    fake_client.run = AsyncMock(return_value=fake_result)

    with patch("tools.scrape.AsyncBrowserUse", return_value=fake_client):
        result = await _scrape_handler(
            {
                "url": "https://acme.com",
                "extraction_goal": "Focus on the B2B decision maker.",
            }
        )

    assert "content" in result
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block["type"] == "text"

    parsed = BrandResearch.model_validate_json(block["text"])
    assert parsed.source_url == "https://acme.com"
    assert parsed.identity.primary_color_hexes == ["#0F62FE", "#161616"]
    assert parsed.creative_copy_idea.headline == "Widgets That Outlast You"

    fake_client.run.assert_awaited_once()
    call_kwargs = fake_client.run.await_args.kwargs
    assert "https://acme.com" in call_kwargs["task"]
    assert "performance marketing researcher" in call_kwargs["task"]
    assert "Problem/Solution" in call_kwargs["task"]
    assert "Focus on the B2B decision maker." in call_kwargs["task"]
    assert call_kwargs["output_schema"] is BrandResearch
    assert call_kwargs["model"] == "claude-opus-4.6"
