import os
from typing import Any

from browser_use_sdk.v3 import AsyncBrowserUse
from claude_agent_sdk import tool

from tools.schemas import BrandResearch

# Process-lifetime cache keyed by (url, extraction_goal). Chainlit keeps the
# Python process alive across turns, so this acts as short-term memory: the
# second time the user asks about the same landing page we return the cached
# BrandResearch JSON instead of round-tripping Browser Use (which costs a
# real headless session). Moves the MaaS "Agent handoffs & memory" parameter
# from L1 (none) to L2 (short-term across tasks in a session).
_SCRAPE_CACHE: dict[tuple[str, str], str] = {}
_SCRAPE_CACHE_LIMIT = 64


def _cache_disabled() -> bool:
    return os.environ.get("AD_PIPELINE_DISABLE_SCRAPE_CACHE", "").lower() in (
        "1",
        "true",
        "yes",
    )


def _cache_put(key: tuple[str, str], value: str) -> None:
    if _cache_disabled():
        return
    if len(_SCRAPE_CACHE) >= _SCRAPE_CACHE_LIMIT:
        # Simple FIFO eviction - good enough for a conversation-length cache.
        _SCRAPE_CACHE.pop(next(iter(_SCRAPE_CACHE)))
    _SCRAPE_CACHE[key] = value


def reset_scrape_cache() -> None:
    """Test hook."""
    _SCRAPE_CACHE.clear()


async def _scrape_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Drive Browser Use Cloud to extract BrandResearch from a landing page."""

    url = args["url"]
    goal = args["extraction_goal"]
    cache_key = (url, goal)
    if not _cache_disabled():
        cached = _SCRAPE_CACHE.get(cache_key)
        if cached is not None:
            return {"content": [{"type": "text", "text": cached}]}

    client = AsyncBrowserUse()
    task = (
        f"Navigate to {url} and act as a performance marketing researcher. "
        "- Brand Identity: find the primary logo URL and 1-3 hex codes for the brand's primary colors. "
        "- Core Value Prop: extract the main headline and the top 3 benefits the product provides. "
        "- Visual Assets: collect URLs for up to 3 high-quality images (product or lifestyle) suitable for ads. Empty list is acceptable if none are usable. "
        "- Tone of Voice: describe the brand's writing style in exactly 3 adjectives. "
        "- Call to Action: identify the primary button text on the page (for example, 'Get Started'). Do NOT translate it - return the literal text. "
        "- Creative Copy Idea: based on the site's content, write one Problem/Solution ad copy variant: Hook (a relatable pain point), Body (how this product solves it), Headline (punchy, benefit-driven, max 40 chars). "
        f"Additional focus: {goal}. "
        "Return all findings as a single BrandResearch JSON object."
    )
    result = await client.run(
        task=task,
        model="claude-opus-4.6",
        output_schema=BrandResearch,
    )
    research: BrandResearch = result.output
    payload = research.model_dump_json()
    _cache_put(cache_key, payload)
    return {"content": [{"type": "text", "text": payload}]}


scrape_url = tool(
    "scrape_url",
    "Scrape a landing URL via Browser Use Cloud and return structured brand research.",
    {"url": str, "extraction_goal": str},
)(_scrape_handler)
