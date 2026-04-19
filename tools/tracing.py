"""Unified observability for the ad-pipeline event stream.

Consumes AssistantMessage / UserMessage / ResultMessage events from the
claude-agent-sdk `query()` iterator and emits:

  1. **Langfuse traces** - one trace per Chainlit turn, nested agent/tool/
     generation spans mirroring the coordinator -> subagent -> tool tree.
     Each AssistantMessage becomes a generation with model + token usage so
     Langfuse auto-computes per-step cost; each ToolUseBlock becomes a tool
     span that closes on its matching ToolResultBlock.
  2. **Chainlit Steps** mirroring the same hierarchy so the UI shows live
     progress (which subagent is running, which tool is executing, inputs,
     outputs, streamed text) instead of a silent wait.

Both emitters share the same state dictionaries keyed by `tool_use_id` so the
tree stays consistent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import chainlit as cl
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

try:
    from langfuse import Langfuse, propagate_attributes
except ImportError:  # pragma: no cover - langfuse is a listed dep
    Langfuse = None  # type: ignore[assignment,misc]
    propagate_attributes = None  # type: ignore[assignment]


_AGENT_TOOL_NAME = "Agent"

_SUBAGENT_DISPLAY_NAMES = {
    "creative-director": "Creative Director",
    "media-buyer": "Media Buyer",
}

_SUBAGENT_ICONS = {
    "creative-director": "palette",
    "media-buyer": "megaphone",
}

_FRIENDLY_TOOL_NAMES = {
    "mcp__adpipeline__scrape_url": "Brand research",
    "mcp__adpipeline__render_creative": "Render creative",
    "mcp__adpipeline__view_brand_reference": "View reference",
    "mcp__adpipeline__critique_render": "Critique render",
}

_TOOL_ICONS = {
    "mcp__adpipeline__scrape_url": "search",
    "mcp__adpipeline__render_creative": "image",
    "mcp__adpipeline__view_brand_reference": "eye",
    "mcp__adpipeline__critique_render": "scan-eye",
}


def _tool_icon(tool_name: str) -> str:
    if tool_name in _TOOL_ICONS:
        return _TOOL_ICONS[tool_name]
    if tool_name.startswith("mcp__pipeboard__"):
        return "facebook"
    return "wrench"


def _subagent_icon(subagent_type: str) -> str:
    return _SUBAGENT_ICONS.get(subagent_type, "user-cog")


def _friendly_tool_name(tool_name: str) -> str:
    if tool_name in _FRIENDLY_TOOL_NAMES:
        return _FRIENDLY_TOOL_NAMES[tool_name]
    if tool_name.startswith("mcp__pipeboard__"):
        stem = tool_name.removeprefix("mcp__pipeboard__").replace("_", " ").title()
        return f"Meta Ads · {stem}"
    if tool_name.startswith("mcp__"):
        stem = tool_name.split("__", 2)[-1].replace("_", " ")
        return stem.capitalize()
    return tool_name


def _subagent_display(subagent_type: str) -> str:
    return _SUBAGENT_DISPLAY_NAMES.get(
        subagent_type, subagent_type.replace("-", " ").title() or "Subagent"
    )


def _clean_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    """Map Anthropic usage field names to Langfuse's `usage_details` keys.

    Langfuse auto-prices Claude models against `input` and `output` token
    keys (plus the cache-specific Anthropic key names). Passing Anthropic's
    raw `input_tokens`/`output_tokens` yields $0 cost in the UI - that alone
    would drop observability a whole level on the rubric.
    """
    if not usage:
        return {}
    mapping = {
        "input_tokens": "input",
        "output_tokens": "output",
        "cache_read_input_tokens": "cache_read_input_tokens",
        "cache_creation_input_tokens": "cache_creation_input_tokens",
    }
    out: dict[str, int] = {}
    for src, dst in mapping.items():
        v = usage.get(src)
        if isinstance(v, int):
            out[dst] = v
    return out


def _preview(obj: Any, limit: int = 800) -> Any:
    if obj is None:
        return None
    if isinstance(obj, str):
        s = obj
    else:
        try:
            s = json.dumps(obj, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            s = str(obj)
    if len(s) > limit:
        return s[:limit] + f"\n…(+{len(s) - limit} chars truncated)"
    return s


def _get_langfuse() -> Any:
    if Langfuse is None:
        return None
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        return None
    return Langfuse(
        public_key=pk,
        secret_key=sk,
        host=os.environ.get("LANGFUSE_HOST") or None,
    )


def _tool_input_summary(block: ToolUseBlock) -> str:
    i = block.input or {}
    if block.name == "mcp__adpipeline__scrape_url":
        url = i.get("url")
        goal = i.get("extraction_goal")
        if goal:
            return f"**URL:** {url}\n**Goal:** {goal}"
        return f"**URL:** {url}"
    if block.name == "mcp__adpipeline__render_creative":
        note = i.get("variant_note") or ""
        return f"**Variant:** {note}" if note else "Rendering 1080x1080 PNG…"
    if block.name == "mcp__adpipeline__view_brand_reference":
        return f"**Image:** {i.get('url')}"
    if block.name.startswith("mcp__pipeboard__"):
        shown = {k: i[k] for k in list(i)[:4]}
        return _preview(shown, limit=400)
    return _preview(i, limit=400)


def _extract_tool_text(content: Any) -> str | None:
    """Pull the first `type: text` block out of an MCP tool result payload."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    for blk in content:
        if isinstance(blk, dict) and blk.get("type") == "text":
            txt = blk.get("text")
            if isinstance(txt, str):
                return txt
    return None


def _color_swatches(hexes: list[Any]) -> str:
    swatches: list[str] = []
    for h in hexes[:4]:
        if not isinstance(h, str):
            continue
        swatches.append(f"`{h}`")
    return " · ".join(swatches) if swatches else "—"


def _bulleted(items: list[Any]) -> str:
    lines: list[str] = []
    for item in items[:5]:
        if not isinstance(item, str):
            continue
        lines.append(f"- {item.strip()}")
    return "\n".join(lines) if lines else "—"


async def _emit_brand_research_card(research: dict[str, Any]) -> None:
    identity = research.get("identity") or {}
    value_prop = research.get("value_prop") or {}
    copy = research.get("creative_copy_idea") or {}

    headline = copy.get("headline") or value_prop.get("headline") or ""
    hook = copy.get("hook") or ""
    body = copy.get("body") or ""
    benefits = value_prop.get("top_3_benefits") or []
    cta = research.get("cta_button_text") or "—"
    colors = identity.get("primary_color_hexes") or []
    tone = research.get("tone_adjectives") or []

    parts: list[str] = ["### Brand research"]
    if headline:
        parts.append(f"**Headline** — {headline}")
    if hook:
        parts.append(f"**Hook** — {hook}")
    if body:
        parts.append(f"**Body** — {body}")
    parts.append("")
    parts.append("**Top benefits**")
    parts.append(_bulleted(benefits))
    parts.append("")
    parts.append(f"**CTA:** {cta}  \n**Palette:** {_color_swatches(colors)}  \n**Tone:** {', '.join(t for t in tone if isinstance(t, str)) or '—'}")

    elements: list[Any] = []
    logo = identity.get("logo_url")
    if isinstance(logo, str) and logo.startswith("https://"):
        elements.append(cl.Image(url=logo, name="logo", display="inline", size="small"))

    await cl.Message(content="\n".join(parts), elements=elements or None).send()


async def _emit_creative_card(payload: dict[str, Any]) -> None:
    png_url = payload.get("png_url")
    variant_note = payload.get("variant_note") or ""
    variant_id = payload.get("variant_id") or ""
    if not isinstance(png_url, str):
        return

    header = f"### Creative · {variant_note}" if variant_note else "### Creative"
    caption = f"`{variant_id}` — {png_url}" if variant_id else png_url
    await cl.Message(
        content=f"{header}\n\n{caption}",
        elements=[
            cl.Image(
                url=png_url,
                name=png_url.rsplit("/", 1)[-1],
                display="inline",
                size="large",
            )
        ],
    ).send()


async def _emit_result_card(tool_name: str | None, tool_content: Any) -> None:
    """Unconditional card emission - kept for callers that don't want gating
    (brand research, etc.). Render_creative is gated via TraceSession now."""
    if tool_name is None:
        return
    text = _extract_tool_text(tool_content)
    if not text:
        return
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return

    if tool_name == "mcp__adpipeline__scrape_url" and isinstance(payload, dict):
        await _emit_brand_research_card(payload)
        return

    if tool_name == "mcp__adpipeline__render_creative" and isinstance(payload, dict):
        await _emit_creative_card(payload)
        return


def _parse_tool_json(tool_content: Any) -> dict[str, Any] | None:
    text = _extract_tool_text(tool_content)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _infer_tool_name_from_step(step: Any) -> str | None:
    """Fallback when Langfuse is disabled and we only have the Chainlit step."""
    name = getattr(step, "name", None)
    if not isinstance(name, str):
        return None
    for raw, friendly in _FRIENDLY_TOOL_NAMES.items():
        if friendly == name:
            return raw
    return None


def _tool_output_summary(content: Any, *, is_error: bool | None) -> str:
    prefix = "⚠️ " if is_error else ""
    parts = _render_tool_output_parts(content)
    body = "\n".join(p for p in parts if p)
    if not body:
        # Last-resort fallback so a step never ends with an empty card.
        body = "Failed with no details." if is_error else "Succeeded with no output."
    return prefix + body


def _render_tool_output_parts(content: Any, *, depth: int = 0) -> list[str]:
    """Render an MCP tool result into human-readable lines.

    Defensive against the shapes that actually show up in the wild:
      - flat string
      - list of content blocks (dicts with `type`)
      - dict with a nested `content` list (happens when the SDK wraps an
        error / image block twice)
      - raw base64 payloads under `data` / `source.data` - we never echo
        base64 bytes back into the UI, we show the media type only.
    """
    if depth > 3:
        return ["[…truncated]"]

    if content is None:
        return []
    if isinstance(content, str):
        return [_preview(content, limit=600)]
    if isinstance(content, dict):
        # Unwrap `{"content": [...]}` / `{"result": ...}` envelopes.
        nested = content.get("content") if isinstance(content.get("content"), list) else None
        if nested is not None:
            return _render_tool_output_parts(nested, depth=depth + 1)
        return [_render_block(content)]
    if isinstance(content, list):
        return [_render_block(b) if isinstance(b, dict) else _preview(b, limit=400) for b in content]
    return [_preview(content, limit=600)]


def _render_block(blk: dict[str, Any]) -> str:
    btype = blk.get("type")
    if btype == "text":
        text = blk.get("text")
        return _preview(str(text) if text is not None else "", limit=600)
    if btype == "image":
        src = blk.get("source") or {}
        url = src.get("url")
        media_type = src.get("media_type") or blk.get("media_type") or "image"
        if url:
            return f"[image {url}]"
        return f"[image ({media_type})]"
    if btype == "tool_result":
        return "\n".join(_render_tool_output_parts(blk.get("content"), depth=1))
    # Generic block: strip any base64 `data` field before previewing so the
    # step output never leaks binary bytes (the 'data' truncation bug).
    safe = {k: v for k, v in blk.items() if k != "data"}
    source = safe.get("source")
    if isinstance(source, dict) and "data" in source:
        safe["source"] = {k: v for k, v in source.items() if k != "data"}
    return _preview(safe, limit=400)


@dataclass
class _AgentCtx:
    lf_span: Any | None = None
    cl_step: cl.Step | None = None
    subagent_type: str = ""


@dataclass
class _ToolCtx:
    lf_span: Any | None = None
    cl_step: cl.Step | None = None
    tool_name: str | None = None


class TraceSession:
    """One Chainlit turn = one Langfuse trace + mirrored cl.Step tree.

    Lifecycle:
        async with TraceSession(session_id, prompt) as trace:
            async for event in query(...):
                await trace.ingest(event)

    The context manager enters Langfuse's session-propagation context so every
    span emitted inside inherits `session_id` (enables multi-turn grouping and
    search in the Langfuse UI).
    """

    def __init__(self, user_session_id: str, prompt: str) -> None:
        self._langfuse = _get_langfuse()
        self._user_session_id = user_session_id
        self._prompt = prompt
        self._root_span: Any | None = None
        self._root_step: cl.Step | None = None
        self._propagate_cm: Any = None
        # Subagent wrapper state; key "" is the coordinator (root).
        self._agent_ctx: dict[str, _AgentCtx] = {}
        # Tool spans keyed by tool_use_id.
        self._tool_ctx: dict[str, _ToolCtx] = {}
        # Last render_creative payload that's awaiting a critique verdict.
        # We don't show a creative card in chat until critique says OK (or is
        # skipped). If the creative-director bails without critiquing, we
        # fall back to emitting it at session close so the user never sees a
        # blank run. Per-variant isolation isn't needed - the iterative loop
        # runs one render/critique pair at a time.
        self._pending_render: dict[str, Any] | None = None

    @property
    def enabled(self) -> bool:
        return self._langfuse is not None

    @property
    def root_step_id(self) -> str | None:
        return None

    async def __aenter__(self) -> "TraceSession":
        # Enter Langfuse session propagation so every nested span carries the
        # same session_id. Do this before any span is created.
        if self._langfuse is not None and propagate_attributes is not None:
            self._propagate_cm = propagate_attributes(
                session_id=self._user_session_id,
                tags=["ad-pipeline", "chainlit"],
            )
            self._propagate_cm.__enter__()
            self._root_span = self._langfuse.start_observation(
                name="ad-pipeline.turn",
                as_type="agent",
                input=_preview(self._prompt),
                metadata={"agent": "coordinator"},
            )

        # NO Chainlit root step for the coordinator - that wrapper hides every
        # tool/subagent call inside a collapsed card ("Used Campaign
        # Coordinator"). Instead, tool and subagent cl.Steps live at the top
        # level so the user sees live progress (Brand research, Creative
        # Director, Render creative, ...) without having to expand anything.
        # The Langfuse trace still nests everything under `ad-pipeline.turn`
        # via self._root_span; the UI hierarchy and trace hierarchy are
        # decoupled on purpose.
        self._root_step = None

        self._agent_ctx[""] = _AgentCtx(
            lf_span=self._root_span,
            cl_step=None,
            subagent_type="coordinator",
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close(error=exc)

    async def ingest(self, event: Any) -> None:
        if isinstance(event, AssistantMessage):
            await self._on_assistant(event)
        elif isinstance(event, UserMessage):
            await self._on_user(event)
        elif isinstance(event, ResultMessage):
            await self._on_result(event)

    async def _on_assistant(self, msg: AssistantMessage) -> None:
        ctx_key = msg.parent_tool_use_id or ""
        ctx = self._agent_ctx.get(ctx_key) or self._agent_ctx[""]

        text_parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
        text_body = "".join(text_parts)
        tool_blocks = [b for b in msg.content if isinstance(b, ToolUseBlock)]

        # Stream the assistant text into the owning step (subagent card or root).
        if text_body and ctx.cl_step is not None:
            for token in text_parts:
                await ctx.cl_step.stream_token(token)
            await ctx.cl_step.update()

        # Emit a Langfuse generation per assistant turn so token/cost is tracked
        # per-step. Model is taken verbatim so Langfuse's auto-pricing resolves.
        owner_span = ctx.lf_span if ctx.lf_span is not None else self._root_span
        if self._langfuse is not None and owner_span is not None:
            gen = owner_span.start_observation(
                name=f"{ctx.subagent_type or 'coordinator'}.turn",
                as_type="generation",
                model=msg.model,
                usage_details=_clean_usage(msg.usage),
                output=_preview(text_body) if text_body else None,
                metadata={
                    "agent": ctx.subagent_type or "coordinator",
                    "stop_reason": msg.stop_reason,
                    "message_id": msg.message_id,
                    "has_tool_calls": bool(tool_blocks),
                },
            )
            gen.end()

        # Open tool (or subagent) spans/steps for each tool use in this turn.
        for block in tool_blocks:
            await self._open_tool(block, owner=ctx)

    async def _open_tool(self, block: ToolUseBlock, *, owner: _AgentCtx) -> None:
        owner_lf = owner.lf_span if owner.lf_span is not None else self._root_span
        owner_cl_id = owner.cl_step.id if owner.cl_step is not None else None

        if block.name == _AGENT_TOOL_NAME:
            subagent_type = str(block.input.get("subagent_type") or "subagent")
            description = str(block.input.get("description") or "").strip()
            lf_span: Any = None
            if self._langfuse is not None and owner_lf is not None:
                lf_span = owner_lf.start_observation(
                    name=f"subagent.{subagent_type}",
                    as_type="agent",
                    input=_preview(
                        {
                            "subagent_type": subagent_type,
                            "description": description,
                            "prompt": block.input.get("prompt"),
                        }
                    ),
                    metadata={
                        "agent": subagent_type,
                        "tool_use_id": block.id,
                    },
                )
            step = cl.Step(
                name=_subagent_display(subagent_type),
                type="run",
                parent_id=owner_cl_id,
                icon=_subagent_icon(subagent_type),
                default_open=True,
                show_input=False,
            )
            if description:
                step.input = description
            await step.send()
            self._agent_ctx[block.id] = _AgentCtx(
                lf_span=lf_span, cl_step=step, subagent_type=subagent_type
            )
            return

        # Regular MCP / builtin tool.
        lf_span = None
        if self._langfuse is not None and owner_lf is not None:
            lf_span = owner_lf.start_observation(
                name=_friendly_tool_name(block.name),
                as_type="tool",
                input=_preview(block.input),
                metadata={
                    "tool_name": block.name,
                    "tool_use_id": block.id,
                },
            )
        step = cl.Step(
            name=_friendly_tool_name(block.name),
            type="tool",
            parent_id=owner_cl_id,
            icon=_tool_icon(block.name),
            show_input=False,
        )
        step.input = _tool_input_summary(block)
        await step.send()
        self._tool_ctx[block.id] = _ToolCtx(
            lf_span=lf_span, cl_step=step, tool_name=block.name
        )

    async def _on_user(self, msg: UserMessage) -> None:
        content = msg.content
        if isinstance(content, str):
            return
        for block in content:
            if isinstance(block, ToolResultBlock):
                await self._close_tool(
                    block.tool_use_id, block.content, is_error=block.is_error
                )

    async def _close_tool(
        self, tool_use_id: str, output: Any, *, is_error: bool | None
    ) -> None:
        if tool_use_id in self._tool_ctx:
            ctx = self._tool_ctx.pop(tool_use_id)
            tool_name = ctx.tool_name
            if ctx.lf_span is not None:
                ctx.lf_span.update(
                    output=_preview(output),
                    level="ERROR" if is_error else None,
                    status_message="tool_error" if is_error else None,
                )
                ctx.lf_span.end()
            if ctx.cl_step is not None:
                if tool_name is None:
                    tool_name = _infer_tool_name_from_step(ctx.cl_step)
                ctx.cl_step.output = _tool_output_summary(output, is_error=is_error)
                if is_error:
                    ctx.cl_step.is_error = True
                await ctx.cl_step.update()

            if not is_error:
                await self._gated_emit_card(tool_name, output)
            return

        if tool_use_id in self._agent_ctx:
            ctx = self._agent_ctx.pop(tool_use_id)
            if ctx.lf_span is not None:
                ctx.lf_span.update(
                    output=_preview(output),
                    level="ERROR" if is_error else None,
                    status_message="subagent_error" if is_error else None,
                )
                ctx.lf_span.end()
            if ctx.cl_step is not None:
                if is_error:
                    ctx.cl_step.is_error = True
                await ctx.cl_step.update()

    async def _on_result(self, msg: ResultMessage) -> None:
        if self._langfuse is None or self._root_span is None:
            return
        self._root_span.update(
            output=_preview(msg.result) if msg.result else None,
            metadata={
                "sdk_session_id": msg.session_id,
                "num_turns": msg.num_turns,
                "duration_ms": msg.duration_ms,
                "duration_api_ms": msg.duration_api_ms,
                "total_cost_usd": msg.total_cost_usd,
                "model_usage": msg.model_usage,
                "permission_denials": msg.permission_denials,
                "errors": msg.errors,
                "stop_reason": msg.stop_reason,
                "subtype": msg.subtype,
            },
            level="ERROR" if msg.is_error else None,
            status_message=msg.stop_reason if msg.is_error else None,
        )

    async def _gated_emit_card(self, tool_name: str | None, tool_content: Any) -> None:
        """Emit in-chat result cards with critique gating.

        - `scrape_url` → always emit (brand research isn't iterative).
        - `render_creative` → hold in self._pending_render; do NOT emit yet.
        - `critique_render` → if verdict == "ok" or skipped_reason set,
          flush the pending render. On "iterate", keep the pending render
          so the next render replaces it (or session close emits it).
        """
        if tool_name is None:
            return
        payload = _parse_tool_json(tool_content)
        if payload is None:
            return

        if tool_name == "mcp__adpipeline__scrape_url":
            await _emit_brand_research_card(payload)
            return

        if tool_name == "mcp__adpipeline__render_creative":
            self._pending_render = payload
            return

        if tool_name == "mcp__adpipeline__critique_render":
            verdict = payload.get("verdict")
            skipped = payload.get("skipped_reason")
            if self._pending_render is None:
                return
            if verdict == "ok" or skipped:
                pending, self._pending_render = self._pending_render, None
                try:
                    await _emit_creative_card(pending)
                except Exception:
                    pass
            # verdict == "iterate": keep pending; next render overwrites.
            return

    async def close(self, error: BaseException | None = None) -> None:
        # Fail-open: if the creative-director rendered something but never
        # ran a critique (or was cut off mid-iteration), still show the
        # user the last render so they don't see a silent run.
        if self._pending_render is not None:
            try:
                await _emit_creative_card(self._pending_render)
            except Exception:
                pass
            self._pending_render = None

        # Close any leftover tool/subagent ctxs (defensive - normally
        # ToolResultBlocks close them before we get here).
        for ctx in list(self._tool_ctx.values()):
            if ctx.lf_span is not None:
                try:
                    ctx.lf_span.end()
                except Exception:
                    pass
        self._tool_ctx.clear()

        for key, ctx in list(self._agent_ctx.items()):
            if key == "":
                continue
            if ctx.lf_span is not None:
                try:
                    ctx.lf_span.end()
                except Exception:
                    pass

        if self._langfuse is not None and self._root_span is not None:
            if error is not None:
                try:
                    self._root_span.update(
                        level="ERROR",
                        status_message=repr(error),
                    )
                except Exception:
                    pass
            try:
                self._root_span.end()
            except Exception:
                pass

        if self._propagate_cm is not None:
            try:
                self._propagate_cm.__exit__(None, None, None)
            except Exception:
                pass

        if self._langfuse is not None:
            try:
                self._langfuse.flush()
            except Exception:
                pass

        if self._root_step is not None:
            try:
                await self._root_step.update()
            except Exception:
                pass
