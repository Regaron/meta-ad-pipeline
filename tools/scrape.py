from typing import Any

from browser_use_sdk.v3 import AsyncBrowserUse
from claude_agent_sdk import tool

from tools.schemas import BrandResearch


async def _scrape_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Drive Browser Use Cloud to extract BrandResearch from a landing page."""

    client = AsyncBrowserUse()
    task = (
        f"Navigate to {args['url']} and act as a performance marketing researcher. "
        "- Brand Identity: find the primary logo URL and 1-3 hex codes for the brand's primary colors. "
        "- Core Value Prop: extract the main headline and the top 3 benefits the product provides. "
        "- Visual Assets: collect URLs for up to 3 high-quality images (product or lifestyle) suitable for ads. Empty list is acceptable if none are usable. "
        "- Tone of Voice: describe the brand's writing style in exactly 3 adjectives. "
        "- Call to Action: identify the primary button text on the page (for example, 'Get Started'). Do NOT translate it - return the literal text. "
        "- Creative Copy Idea: based on the site's content, write one Problem/Solution ad copy variant: Hook (a relatable pain point), Body (how this product solves it), Headline (punchy, benefit-driven, max 40 chars). "
        f"Additional focus: {args['extraction_goal']}. "
        "Return all findings as a single BrandResearch JSON object."
    )
    result = await client.run(
        task=task,
        model="claude-opus-4.6",
        output_schema=BrandResearch,
    )
    research: BrandResearch = result.output
    return {"content": [{"type": "text", "text": research.model_dump_json()}]}


scrape_url = tool(
    "scrape_url",
    "Scrape a landing URL via Browser Use Cloud and return structured brand research.",
    {"url": str, "extraction_goal": str},
)(_scrape_handler)
