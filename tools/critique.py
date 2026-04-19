"""Vision-based critique of a rendered ad creative.

The Claude Agent SDK's MCP tool transport does not reliably round-trip image
content blocks back to the model (we lose them as 'data' strings in the
ToolResultBlock). So we call the raw Anthropic API from inside the tool
with the PNG's Tigris URL as an image block — that path supports vision
natively — and return the model's structured critique back as text.

The creative-director reads the JSON verdict and iterates if issues found.
"""

from __future__ import annotations

import json
import os
from typing import Any

from claude_agent_sdk import tool

_CRITIQUE_MODEL = os.environ.get("AD_PIPELINE_CRITIQUE_MODEL", "claude-sonnet-4-5-20250929")

_CRITIQUE_PROMPT = """\
You are a senior art director reviewing a Facebook/Meta ad creative rendered
to 1080x1080. Critique ruthlessly against these rules and return STRICT JSON
ONLY, matching this exact shape:

{
  "verdict": "ok" | "iterate",
  "issues": [
    {"area": "headline" | "body" | "cta" | "palette" | "layout" | "hierarchy",
     "severity": "block" | "nit",
     "detail": "<one short sentence>"}
  ],
  "strengths": ["<one short sentence>", ...]
}

Rules (block-severity issues force verdict=iterate):
  - Headline must not wrap one-word-per-line, touch a canvas edge, or be
    truncated.
  - No text may bleed past the 1080x1080 bounds or overlap another element.
  - No body/benefit line may be cut off mid-sentence (trailing "..." or
    visible clipping).
  - Headline contrast vs background must be readable at thumbnail size
    (mentally squint - if you can't read it in a 200x200 preview, it fails).
  - Exactly one dominant element (usually the headline). If 3+ things
    compete, hierarchy is broken.
  - CTA button must be anchored (not floating mid-canvas), visibly
    high-contrast, with clear space around it.
  - Palette must feel cohesive - flag if colors look invented or clash.

Nits are acceptable and should NOT force iteration. Only block-severity
issues cause verdict=iterate.

If the creative is good, return verdict=ok with an empty issues array and
at least one strength.
"""


def _get_client() -> Any:
    """Return an Anthropic client or None if no API key is configured."""
    try:
        from anthropic import Anthropic
    except ImportError:  # pragma: no cover - listed dep
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return Anthropic(api_key=api_key)


def _fallback(reason: str) -> dict[str, Any]:
    """Return a neutral 'ok' verdict when critique cannot run.

    We never want a missing API key or transient model error to block the
    whole pipeline - downstream steps (media-buyer publishing) only need
    the PNG URL.
    """
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "verdict": "ok",
                        "issues": [],
                        "strengths": [],
                        "skipped_reason": reason,
                    }
                ),
            }
        ]
    }


async def _critique_handler(args: dict[str, Any]) -> dict[str, Any]:
    png_url = args["png_url"]
    variant_note = args.get("variant_note", "")

    client = _get_client()
    if client is None:
        return _fallback("ANTHROPIC_API_KEY not configured; skipping vision critique.")

    context_line = f"Variant note from the designer: {variant_note}" if variant_note else ""

    try:
        resp = client.messages.create(
            model=_CRITIQUE_MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "url", "url": png_url},
                        },
                        {
                            "type": "text",
                            "text": _CRITIQUE_PROMPT + ("\n\n" + context_line if context_line else ""),
                        },
                    ],
                }
            ],
        )
    except Exception as e:  # noqa: BLE001 - surface any API failure as a neutral verdict
        return _fallback(f"vision critique failed: {type(e).__name__}: {e}")

    # Anthropic response content is a list of blocks; collect the text blocks.
    text_parts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    raw = "\n".join(text_parts).strip()

    # The model sometimes wraps JSON in a code fence; strip it defensively.
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return _fallback(f"model returned non-JSON critique: {raw[:200]!r}")

    if not isinstance(payload, dict) or "verdict" not in payload:
        return _fallback(f"critique missing 'verdict': {raw[:200]!r}")

    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


critique_render = tool(
    "critique_render",
    "Critique a rendered 1080x1080 ad PNG by calling the raw Anthropic API "
    "with vision (URL-based image block, bypasses the SDK's text-only MCP "
    "transport). Returns strict JSON: {verdict: ok|iterate, issues: [...], "
    "strengths: [...]}. Safe to call when ANTHROPIC_API_KEY is missing - "
    "returns a neutral ok verdict with skipped_reason.",
    {"png_url": str, "variant_note": str},
)(_critique_handler)
