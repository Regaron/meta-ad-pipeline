from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from claude_agent_sdk import tool

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _validate_reference_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return "view_brand_reference only accepts https image URLs."

    if any(parsed.path.lower().endswith(suffix) for suffix in _IMAGE_SUFFIXES):
        return None

    request = Request(url, method="HEAD")
    try:
        with urlopen(request, timeout=3) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
    except (HTTPError, URLError, ValueError) as exc:
        return f"view_brand_reference rejected {url}: {exc}"

    if content_type.startswith("image/"):
        return None

    return (
        f"view_brand_reference rejected {url}: "
        f"Content-Type {content_type or 'unknown'} is not an image."
    )


async def _view_brand_reference_handler(args: dict[str, Any]) -> dict[str, Any]:
    url = args["url"]
    error = _validate_reference_url(url)
    if error is not None:
        return {"content": [{"type": "text", "text": error}]}
    return {
        "content": [
            {"type": "image", "source": {"type": "url", "url": url}}
        ]
    }


view_brand_reference = tool(
    "view_brand_reference",
    "Return an image content block for an https brand-reference URL.",
    {"url": str},
)(_view_brand_reference_handler)
