import json
from unittest.mock import AsyncMock, patch

import pytest

from tools.schemas import AdCopy


_CANNED_COPY = AdCopy(
    headline="Premium Widgets — 20% Off",
    primary_text="High-grade widgets built to last.",
    description="Shop widgets.",
    value_props=["Free shipping", "Lifetime warranty", "30-day returns"],
    call_to_action="SHOP_NOW",
    brand_color_theme="warm sunset oranges",
)


@pytest.mark.asyncio
async def test_scrape_url_returns_text_block_with_adcopy_json():
    """scrape_url wraps browser-use-sdk and returns an AdCopy serialized to JSON text."""
    from tools.scrape import scrape_url

    fake_result = AsyncMock()
    fake_result.output = _CANNED_COPY

    fake_client = AsyncMock()
    fake_client.run = AsyncMock(return_value=fake_result)

    with patch("tools.scrape.AsyncBrowserUse", return_value=fake_client):
        # Call the underlying coroutine directly to sidestep SdkMcpTool internals.
        from tools.scrape import _scrape_handler

        result = await _scrape_handler(
            {"url": "https://acme.com", "extraction_goal": "Extract ad copy."}
        )

    assert "content" in result
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block["type"] == "text"

    parsed = AdCopy.model_validate_json(block["text"])
    assert parsed.headline == "Premium Widgets — 20% Off"
    assert parsed.call_to_action == "SHOP_NOW"

    fake_client.run.assert_awaited_once()
    call_kwargs = fake_client.run.await_args.kwargs
    assert "https://acme.com" in call_kwargs["task"]
    assert call_kwargs["output_schema"] is AdCopy
    assert call_kwargs["model"] == "claude-opus-4.6"
