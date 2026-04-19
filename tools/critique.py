"""Vision-based critique of a rendered ad creative.

The Claude Agent SDK's MCP *tool-result* transport does not reliably
round-trip image content blocks back to the model (we lose them as 'data'
strings in the ToolResultBlock). *User-message* content blocks, however,
flow through cleanly because they go straight to Claude without MCP
rewriting.

So this tool runs a one-shot `query()` against the bundled Claude Code CLI
with the Tigris PNG URL as an image block in the user message. That path
uses whatever auth the CLI is already logged into (OAuth via Pro/Max
subscription, or ANTHROPIC_API_KEY if set) - no extra API key needed.

The creative-director reads the JSON verdict and iterates if issues found.

Fallback order on each call:
  1. claude-agent-sdk query() with image in user message (OAuth-first).
  2. Raw anthropic.Anthropic() client with ANTHROPIC_API_KEY (no CLI).
  3. Neutral verdict=ok with skipped_reason so the pipeline never blocks.
"""

from __future__ import annotations

import json
import os
from typing import Any

from claude_agent_sdk import tool

_CRITIQUE_MODEL = os.environ.get("AD_PIPELINE_CRITIQUE_MODEL", "claude-sonnet-4-5-20250929")

_CRITIQUE_PROMPT = """\
You are a senior art director reviewing a Facebook/Meta ad creative rendered
to 1080x1080. You MUST actually look at the image. If you cannot see an
image at all, return verdict=iterate with an issue
{area:"layout", severity:"block", detail:"image not received"} — do not
bluff.

Output STRICT JSON ONLY in this exact shape (no prose, no code fences):

{
  "observations": {
    "headline_text": "<the literal headline text you see, verbatim>",
    "headline_lines": <integer: visual lines the headline occupies>,
    "dominant_colors": ["<hex or color name>", "..."],
    "cta_position": "<e.g. 'bottom-right', 'mid-canvas', 'none'>",
    "elements_overlapping": <true | false>,
    "text_bleeding_past_edges": <true | false>,
    "body_lines_truncated": <true | false>,
    "one_word_per_line_wrapping": <true | false>
  },
  "verdict": "ok" | "iterate",
  "issues": [
    {"area": "headline" | "body" | "cta" | "palette" | "layout" | "hierarchy" | "typography",
     "severity": "block" | "nit",
     "detail": "<one short sentence grounded in an observation above>"}
  ],
  "strengths": ["<one short sentence grounded in a specific observation>", ...]
}

Fill `observations` HONESTLY before deciding. The verdict must follow
from the observations. Err on iterate when any observation is uncertain -
never default to ok in doubt.

Block-severity (force verdict=iterate) when ANY are true:
  - observations.elements_overlapping
  - observations.text_bleeding_past_edges
  - observations.body_lines_truncated
  - observations.one_word_per_line_wrapping
  - observations.headline_lines > 3 for a short headline (<=6 words)
  - Headline contrast visibly under ~4.5:1 (squint test at thumbnail)
  - Three or more elements compete for dominance (no single focal point)
  - CTA floating mid-canvas with no anchor, OR cramped against body copy
  - Palette clearly not from a plausible brand primary set
  - Generic AI-slop: centered-stack-on-gradient background, unmotivated
    glassmorphism, reflex-blue (#3B82F6) or purple-to-pink gradient bg

Nits (do NOT force iteration): subtle spacing, minor tracking, small
contrast shortfalls on decorative elements.

Return verdict=ok ONLY when every observation shows zero block-severity
issues AND at least one strength cites a specific observation (not
generic phrases like "clean layout" or "nice typography").
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


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


async def _critique_via_claude_code(png_url: str, prompt_text: str) -> tuple[str | None, str | None]:
    """Return (raw_text, error_reason). On success: (text, None)."""
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
            query,
        )
    except ImportError as e:  # pragma: no cover
        return None, f"claude_agent_sdk import failed: {e}"

    async def _envelope():
        yield {
            "type": "user",
            "session_id": "",
            "message": {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "url", "url": png_url}},
                    {"type": "text", "text": prompt_text},
                ],
            },
            "parent_tool_use_id": None,
        }

    options = ClaudeAgentOptions(
        model=_CRITIQUE_MODEL,
        permission_mode="bypassPermissions",
        setting_sources=[],
    )

    text_parts: list[str] = []
    try:
        async for event in query(prompt=_envelope(), options=options):
            if isinstance(event, AssistantMessage):
                # Only take top-level (non-subagent) text for the critique.
                if getattr(event, "parent_tool_use_id", None):
                    continue
                for block in event.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"

    raw = "".join(text_parts).strip()
    if not raw:
        return None, "claude-code returned no text"
    return raw, None


async def _critique_via_anthropic_api(png_url: str, prompt_text: str) -> tuple[str | None, str | None]:
    client = _get_client()
    if client is None:
        return None, "ANTHROPIC_API_KEY not configured"
    try:
        resp = client.messages.create(
            model=_CRITIQUE_MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "url", "url": png_url}},
                        {"type": "text", "text": prompt_text},
                    ],
                }
            ],
        )
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"

    text_parts: list[str] = []
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    raw = "\n".join(text_parts).strip()
    if not raw:
        return None, "api returned no text"
    return raw, None


async def _critique_handler(args: dict[str, Any]) -> dict[str, Any]:
    png_url = args["png_url"]
    variant_note = args.get("variant_note", "")
    context_line = f"Variant note from the designer: {variant_note}" if variant_note else ""
    prompt_text = _CRITIQUE_PROMPT + ("\n\n" + context_line if context_line else "")

    # Primary path: Claude Code CLI via claude-agent-sdk (uses OAuth/login).
    raw, err_cc = await _critique_via_claude_code(png_url, prompt_text)
    # Fallback: raw Anthropic API via ANTHROPIC_API_KEY.
    if raw is None:
        raw, err_api = await _critique_via_anthropic_api(png_url, prompt_text)
        if raw is None:
            return _fallback(
                f"vision critique unavailable (claude-code: {err_cc}; anthropic-api: {err_api})"
            )

    raw = _strip_code_fence(raw)

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
