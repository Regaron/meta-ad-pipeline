from typing import Any

from browser_use_sdk.v3 import AsyncBrowserUse
from claude_agent_sdk import tool

from tools.schemas import AdCopy


async def _scrape_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Drive Browser Use Cloud to extract AdCopy from a landing page.

    Exposed as a plain coroutine so tests can call it without depending on
    SdkMcpTool internals. The @tool-decorated `scrape_url` below wraps it
    for registration with create_sdk_mcp_server.
    """
    client = AsyncBrowserUse()
    task = (
        f"Visit {args['url']} and extract ad-copy material for a Facebook/Meta ad. "
        f"Additional focus: {args['extraction_goal']}. "
        "Extract a catchy headline (<=40 chars), primary body text (<=125 chars), "
        "short link description (<=30 chars), 3-5 value-prop bullets, a Facebook CTA "
        "(LEARN_MORE / SHOP_NOW / SIGN_UP / DOWNLOAD / GET_OFFER / BOOK_TRAVEL / "
        "CONTACT_US / SUBSCRIBE), and a short brand-color-theme phrase."
    )
    result = await client.run(
        task=task,
        model="claude-opus-4.6",
        output_schema=AdCopy,
    )
    ad_copy: AdCopy = result.output
    return {
        "content": [
            {"type": "text", "text": ad_copy.model_dump_json()}
        ]
    }


scrape_url = tool(
    "scrape_url",
    "Scrape a landing URL via Browser Use Cloud and return structured ad copy.",
    {"url": str, "extraction_goal": str},
)(_scrape_handler)
