"""Tests for tools.tracing.TraceSession.

We don't hit real Langfuse or Chainlit here - both dependencies are stubbed so
the tracer can be exercised headless and we can introspect the emitted span /
step tree.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake langfuse + chainlit modules. Installed before importing tools.tracing.
# ---------------------------------------------------------------------------


class _FakeObservation:
    def __init__(self, parent: "_FakeObservation | None", **kwargs: Any) -> None:
        self.parent = parent
        self.kwargs = dict(kwargs)
        self.updates: list[dict[str, Any]] = []
        self.children: list["_FakeObservation"] = []
        self.ended = False

    @property
    def name(self) -> str:
        return self.kwargs.get("name", "")

    @property
    def as_type(self) -> str:
        return self.kwargs.get("as_type", "span")

    def start_observation(self, **kwargs: Any) -> "_FakeObservation":
        child = _FakeObservation(parent=self, **kwargs)
        self.children.append(child)
        return child

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)

    def end(self) -> None:
        self.ended = True


class _FakeLangfuse:
    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.root_observations: list[_FakeObservation] = []
        self.flushed = 0

    def start_observation(self, **kwargs: Any) -> _FakeObservation:
        obs = _FakeObservation(parent=None, **kwargs)
        self.root_observations.append(obs)
        return obs

    def flush(self) -> None:
        self.flushed += 1


@contextmanager
def _fake_propagate_attributes(**kwargs: Any):
    _fake_propagate_attributes.last_kwargs = kwargs  # type: ignore[attr-defined]
    yield


_fake_propagate_attributes.last_kwargs = None  # type: ignore[attr-defined]


def _install_fake_langfuse() -> _FakeLangfuse:
    fake_module = types.ModuleType("langfuse")
    fake_module.Langfuse = _FakeLangfuse  # type: ignore[attr-defined]
    fake_module.propagate_attributes = _fake_propagate_attributes  # type: ignore[attr-defined]
    sys.modules["langfuse"] = fake_module
    return fake_module  # type: ignore[return-value]


@dataclass
class _FakeStep:
    name: str = ""
    type: str = "undefined"
    parent_id: str | None = None
    icon: str | None = None
    default_open: bool = False
    show_input: bool | str = "json"
    id: str = field(default="step-id")
    input: Any = None
    output: Any = None
    is_error: bool = False
    streamed: list[str] = field(default_factory=list)
    sent: int = 0
    updated: int = 0

    def __post_init__(self) -> None:
        _FakeStep.instances.append(self)  # type: ignore[attr-defined]
        self.id = f"step-{len(_FakeStep.instances)}"  # type: ignore[attr-defined]

    async def send(self) -> None:
        self.sent += 1

    async def update(self) -> None:
        self.updated += 1

    async def stream_token(self, token: str, is_sequence: bool = False, is_input: bool = False) -> None:  # noqa: ARG002
        self.streamed.append(token)


_FakeStep.instances = []  # type: ignore[attr-defined]


@dataclass
class _FakeMessage:
    content: str = ""
    elements: Any = None
    sent: int = 0

    def __post_init__(self) -> None:
        _FakeMessage.instances.append(self)  # type: ignore[attr-defined]

    async def send(self) -> "_FakeMessage":
        self.sent += 1
        return self


_FakeMessage.instances = []  # type: ignore[attr-defined]


@dataclass
class _FakeImage:
    url: str = ""
    name: str = ""
    display: str = "inline"
    size: str | None = None


def _install_fake_chainlit() -> None:
    fake_module = types.ModuleType("chainlit")

    def _step_init(**kwargs: Any) -> _FakeStep:
        return _FakeStep(**kwargs)

    def _msg_init(**kwargs: Any) -> _FakeMessage:
        return _FakeMessage(**kwargs)

    def _image_init(**kwargs: Any) -> _FakeImage:
        return _FakeImage(**kwargs)

    fake_module.Step = _step_init  # type: ignore[attr-defined]
    fake_module.Message = _msg_init  # type: ignore[attr-defined]
    fake_module.Image = _image_init  # type: ignore[attr-defined]
    sys.modules["chainlit"] = fake_module


# ---------------------------------------------------------------------------
# Fixtures + event builders.
# ---------------------------------------------------------------------------


@pytest.fixture
def tracing(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    _install_fake_langfuse()
    _install_fake_chainlit()
    _FakeStep.instances.clear()  # type: ignore[attr-defined]

    # Clear import cache so tracing.py picks up the fakes.
    sys.modules.pop("tools.tracing", None)
    import tools.tracing as tracing_module

    return tracing_module


def _assistant(
    *,
    text: str | None = None,
    model: str = "claude-opus-4-7",
    parent_tool_use_id: str | None = None,
    tool_uses: list[dict[str, Any]] | None = None,
    usage: dict[str, int] | None = None,
    stop_reason: str | None = "end_turn",
):
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

    content: list[Any] = []
    if text is not None:
        content.append(TextBlock(text=text))
    for tool in tool_uses or []:
        content.append(
            ToolUseBlock(id=tool["id"], name=tool["name"], input=tool.get("input", {}))
        )
    return AssistantMessage(
        content=content,
        model=model,
        parent_tool_use_id=parent_tool_use_id,
        usage=usage or {"input_tokens": 10, "output_tokens": 20},
        message_id="msg-1",
        stop_reason=stop_reason,
        session_id="sdk-session-1",
    )


def _user_tool_result(tool_use_id: str, content: Any, is_error: bool | None = None):
    from claude_agent_sdk import ToolResultBlock, UserMessage

    return UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id=tool_use_id, content=content, is_error=is_error
            )
        ],
        parent_tool_use_id=None,
        tool_use_result=None,
    )


def _result(total_cost_usd: float = 0.01, is_error: bool = False):
    from claude_agent_sdk import ResultMessage

    return ResultMessage(
        subtype="success" if not is_error else "error",
        duration_ms=1000,
        duration_api_ms=800,
        is_error=is_error,
        num_turns=3,
        session_id="sdk-session-1",
        stop_reason="end_turn",
        total_cost_usd=total_cost_usd,
        usage={"input_tokens": 100, "output_tokens": 200},
        result="final answer",
        structured_output=None,
        model_usage={"claude-opus-4-7": {"input_tokens": 100}},
        permission_denials=None,
        errors=None,
    )


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


async def test_no_keys_disables_langfuse(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    _install_fake_langfuse()
    _install_fake_chainlit()
    _FakeStep.instances.clear()  # type: ignore[attr-defined]
    sys.modules.pop("tools.tracing", None)
    import tools.tracing as tracing_module

    async with tracing_module.TraceSession(
        user_session_id="session-abc", prompt="hello"
    ) as trace:
        assert trace.enabled is False
        # No root coordinator step is created - tool/subagent steps live at
        # the top level instead so the user sees live progress without
        # expanding anything. An assistant turn with no tool calls emits no
        # Chainlit steps at all.
        assert len(_FakeStep.instances) == 0  # type: ignore[attr-defined]
        await trace.ingest(_assistant(text="hi"))
        await trace.ingest(_result())


async def test_root_trace_wraps_session_and_prompt(tracing):
    async with tracing.TraceSession(
        user_session_id="session-abc", prompt="build an ad for https://x.com"
    ) as trace:
        assert trace.enabled is True
        # propagate_attributes receives the session id.
        assert _fake_propagate_attributes.last_kwargs["session_id"] == "session-abc"  # type: ignore[attr-defined]

        fake = sys.modules["langfuse"].Langfuse  # type: ignore[attr-defined]
        # find our singleton: it was instantiated inside TraceSession
        # We can reach it via trace._langfuse.
        lf = trace._langfuse
        assert isinstance(lf, fake)
        assert len(lf.root_observations) == 1
        root = lf.root_observations[0]
        assert root.as_type == "agent"
        assert root.kwargs["name"] == "ad-pipeline.turn"
        assert "build an ad" in root.kwargs["input"]

        await trace.ingest(_result())

    assert root.ended is True
    assert lf.flushed == 1


async def test_simple_tool_use_creates_and_closes_tool_span(tracing):
    async with tracing.TraceSession(
        user_session_id="s", prompt="go"
    ) as trace:
        tool_use_id = "toolu_1"
        await trace.ingest(
            _assistant(
                text="Scraping…",
                tool_uses=[
                    {
                        "id": tool_use_id,
                        "name": "mcp__adpipeline__scrape_url",
                        "input": {"url": "https://example.com", "extraction_goal": "brand"},
                    }
                ],
            )
        )
        await trace.ingest(
            _user_tool_result(tool_use_id, [{"type": "text", "text": '{"brand":"x"}'}])
        )
        await trace.ingest(_result())

    root = trace._langfuse.root_observations[0]
    # Root has: (1) coordinator generation (2) tool span
    gens = [c for c in root.children if c.as_type == "generation"]
    tools = [c for c in root.children if c.as_type == "tool"]
    assert len(gens) == 1
    assert len(tools) == 1
    assert tools[0].kwargs["name"] == "Brand research"
    assert tools[0].ended is True
    # Output on tool span was set via update().
    assert any("output" in u for u in tools[0].updates)


async def test_subagent_delegation_creates_agent_wrapper_and_nests_children(tracing):
    async with tracing.TraceSession(
        user_session_id="s", prompt="delegate"
    ) as trace:
        agent_call_id = "toolu_agent_1"
        tool_call_id = "toolu_render_1"

        # Coordinator turn: decides to delegate to creative-director.
        await trace.ingest(
            _assistant(
                text="Delegating to CD",
                tool_uses=[
                    {
                        "id": agent_call_id,
                        "name": "Agent",
                        "input": {
                            "subagent_type": "creative-director",
                            "description": "Render 2 variants",
                            "prompt": "…",
                        },
                    }
                ],
            )
        )

        # Subagent turn: child of the Agent tool_use_id.
        await trace.ingest(
            _assistant(
                text="Rendering variant 1",
                parent_tool_use_id=agent_call_id,
                tool_uses=[
                    {
                        "id": tool_call_id,
                        "name": "mcp__adpipeline__render_creative",
                        "input": {"html": "<html>…</html>", "variant_note": "A"},
                    }
                ],
            )
        )

        # Tool result for render (inside subagent).
        await trace.ingest(
            _user_tool_result(
                tool_call_id,
                [{"type": "text", "text": '{"png_url":"https://…"}'}],
            )
        )

        # Subagent returns its summary to coordinator (tool result for Agent).
        await trace.ingest(
            _user_tool_result(
                agent_call_id,
                [{"type": "text", "text": '[{"variant_id":"A"}]'}],
            )
        )

        await trace.ingest(_result())

    root = trace._langfuse.root_observations[0]
    agent_children = [c for c in root.children if c.as_type == "agent"]
    assert len(agent_children) == 1
    subagent = agent_children[0]
    assert subagent.kwargs["name"] == "subagent.creative-director"

    # Inside the subagent wrapper: a generation + a tool span, both nested.
    sub_gens = [c for c in subagent.children if c.as_type == "generation"]
    sub_tools = [c for c in subagent.children if c.as_type == "tool"]
    assert len(sub_gens) == 1
    assert sub_gens[0].kwargs["metadata"]["agent"] == "creative-director"
    assert len(sub_tools) == 1
    assert sub_tools[0].kwargs["name"] == "Render creative"
    assert sub_tools[0].ended is True
    assert subagent.ended is True


async def test_tool_error_flags_level_and_status(tracing):
    async with tracing.TraceSession(user_session_id="s", prompt="go") as trace:
        tool_use_id = "toolu_err"
        await trace.ingest(
            _assistant(
                text="Trying",
                tool_uses=[
                    {
                        "id": tool_use_id,
                        "name": "mcp__adpipeline__scrape_url",
                        "input": {"url": "https://bad", "extraction_goal": "x"},
                    }
                ],
            )
        )
        await trace.ingest(
            _user_tool_result(tool_use_id, "browser timed out", is_error=True)
        )
        await trace.ingest(_result(is_error=True))

    root = trace._langfuse.root_observations[0]
    tools = [c for c in root.children if c.as_type == "tool"]
    update = tools[0].updates[-1]
    assert update["level"] == "ERROR"
    assert update["status_message"] == "tool_error"

    root_updates = root.updates
    assert any(u.get("level") == "ERROR" for u in root_updates)


async def test_result_metadata_captures_cost_and_turns(tracing):
    async with tracing.TraceSession(user_session_id="s", prompt="go") as trace:
        await trace.ingest(_assistant(text="done", tool_uses=[]))
        await trace.ingest(_result(total_cost_usd=0.37))

    root = trace._langfuse.root_observations[0]
    merged = {k: v for u in root.updates for k, v in u.items()}
    metadata = merged["metadata"]
    assert metadata["total_cost_usd"] == 0.37
    assert metadata["num_turns"] == 3
    assert metadata["duration_ms"] == 1000


async def test_scrape_url_result_emits_brand_research_card(tracing):
    _FakeMessage.instances.clear()  # type: ignore[attr-defined]
    async with tracing.TraceSession(user_session_id="s", prompt="go") as trace:
        tool_use_id = "toolu_scrape"
        await trace.ingest(
            _assistant(
                text="Scraping",
                tool_uses=[
                    {
                        "id": tool_use_id,
                        "name": "mcp__adpipeline__scrape_url",
                        "input": {"url": "https://x.com", "extraction_goal": "brand"},
                    }
                ],
            )
        )
        research = {
            "identity": {"logo_url": "https://cdn.x/logo.png", "primary_color_hexes": ["#112233", "#445566"]},
            "value_prop": {"headline": "Real-time data", "top_3_benefits": ["Fast", "Reliable", "Scales"]},
            "tone_adjectives": ["bold", "clear", "human"],
            "cta_button_text": "Start free",
            "creative_copy_idea": {
                "headline": "Ship faster",
                "hook": "Tired of waiting?",
                "body": "Cut deploy times by 10x",
            },
        }
        import json

        await trace.ingest(
            _user_tool_result(
                tool_use_id, [{"type": "text", "text": json.dumps(research)}]
            )
        )
        await trace.ingest(_result())

    msgs = _FakeMessage.instances  # type: ignore[attr-defined]
    cards = [m for m in msgs if "Brand research" in (m.content or "")]
    assert len(cards) == 1
    card = cards[0]
    assert "Ship faster" in card.content
    assert "Tired of waiting?" in card.content
    assert "- Fast" in card.content
    assert "#112233" in card.content
    assert card.sent == 1


async def test_render_without_critique_emits_card_on_session_close(tracing):
    """If render happens but no critique ever runs, we fall back to emitting
    the last render at session close so the user isn't left with nothing."""
    _FakeMessage.instances.clear()  # type: ignore[attr-defined]
    payload = {
        "variant_id": "abc123",
        "variant_note": "Warm, lifestyle",
        "png_url": "https://bucket.t3.tigrisfiles.io/creatives/abc123.png",
    }
    import json

    async with tracing.TraceSession(user_session_id="s", prompt="go") as trace:
        tool_use_id = "toolu_render_1"
        await trace.ingest(
            _assistant(
                text="Rendering",
                tool_uses=[
                    {
                        "id": tool_use_id,
                        "name": "mcp__adpipeline__render_creative",
                        "input": {"html": "<html/>", "variant_note": "A/B split"},
                    }
                ],
            )
        )
        await trace.ingest(
            _user_tool_result(
                tool_use_id, [{"type": "text", "text": json.dumps(payload)}]
            )
        )
        # No critique tool call happens before ResultMessage: the pending
        # render is released on session close.
        msgs_mid = [m for m in _FakeMessage.instances if "Creative" in (m.content or "")]  # type: ignore[attr-defined]
        assert msgs_mid == [], "creative card must not emit before critique or close"
        await trace.ingest(_result())

    cards = [m for m in _FakeMessage.instances if "Creative" in (m.content or "")]  # type: ignore[attr-defined]
    assert len(cards) == 1
    assert "Warm, lifestyle" in cards[0].content
    assert cards[0].elements[0].url == payload["png_url"]


async def test_render_iterate_then_ok_emits_only_final_variant(tracing):
    """First render gets verdict=iterate (no card). Second render gets
    verdict=ok (card emits). Iteration should hide the intermediate."""
    _FakeMessage.instances.clear()  # type: ignore[attr-defined]
    first = {
        "variant_id": "draft1",
        "variant_note": "v1 - too cramped",
        "png_url": "https://cdn/draft1.png",
    }
    final = {
        "variant_id": "final1",
        "variant_note": "v2 - fixed headline wrapping",
        "png_url": "https://cdn/final1.png",
    }
    import json

    async with tracing.TraceSession(user_session_id="s", prompt="go") as trace:
        # First render.
        await trace.ingest(
            _assistant(
                tool_uses=[{
                    "id": "r1",
                    "name": "mcp__adpipeline__render_creative",
                    "input": {"html": "<x/>", "variant_note": "v1"},
                }],
            )
        )
        await trace.ingest(_user_tool_result("r1", [{"type": "text", "text": json.dumps(first)}]))

        # First critique: iterate.
        await trace.ingest(
            _assistant(
                tool_uses=[{
                    "id": "c1",
                    "name": "mcp__adpipeline__critique_render",
                    "input": {"png_url": first["png_url"], "variant_note": "v1"},
                }],
            )
        )
        iterate_json = json.dumps({
            "verdict": "iterate",
            "issues": [{"area": "headline", "severity": "block", "detail": "wraps"}],
            "strengths": [],
        })
        await trace.ingest(_user_tool_result("c1", [{"type": "text", "text": iterate_json}]))

        # Second render.
        await trace.ingest(
            _assistant(
                tool_uses=[{
                    "id": "r2",
                    "name": "mcp__adpipeline__render_creative",
                    "input": {"html": "<x/>", "variant_note": "v2"},
                }],
            )
        )
        await trace.ingest(_user_tool_result("r2", [{"type": "text", "text": json.dumps(final)}]))

        # Second critique: ok.
        await trace.ingest(
            _assistant(
                tool_uses=[{
                    "id": "c2",
                    "name": "mcp__adpipeline__critique_render",
                    "input": {"png_url": final["png_url"], "variant_note": "v2"},
                }],
            )
        )
        ok_json = json.dumps({"verdict": "ok", "issues": [], "strengths": ["clear"]})
        await trace.ingest(_user_tool_result("c2", [{"type": "text", "text": ok_json}]))

        await trace.ingest(_result())

    cards = [m for m in _FakeMessage.instances if "Creative" in (m.content or "")]  # type: ignore[attr-defined]
    # Only the accepted variant shows up - draft1 never got a card.
    assert len(cards) == 1
    assert final["variant_id"] in cards[0].content
    assert "draft1" not in cards[0].content


async def test_critique_skipped_still_emits_card(tracing):
    """If critique was skipped (e.g. missing API key), fall open and emit."""
    _FakeMessage.instances.clear()  # type: ignore[attr-defined]
    payload = {"variant_id": "x", "variant_note": "v1", "png_url": "https://cdn/x.png"}
    import json

    async with tracing.TraceSession(user_session_id="s", prompt="go") as trace:
        await trace.ingest(
            _assistant(
                tool_uses=[{
                    "id": "r1",
                    "name": "mcp__adpipeline__render_creative",
                    "input": {"html": "<x/>", "variant_note": "v1"},
                }],
            )
        )
        await trace.ingest(_user_tool_result("r1", [{"type": "text", "text": json.dumps(payload)}]))
        await trace.ingest(
            _assistant(
                tool_uses=[{
                    "id": "c1",
                    "name": "mcp__adpipeline__critique_render",
                    "input": {"png_url": payload["png_url"], "variant_note": "v1"},
                }],
            )
        )
        skipped_json = json.dumps({
            "verdict": "ok",
            "issues": [],
            "strengths": [],
            "skipped_reason": "ANTHROPIC_API_KEY not configured",
        })
        await trace.ingest(_user_tool_result("c1", [{"type": "text", "text": skipped_json}]))
        await trace.ingest(_result())

    cards = [m for m in _FakeMessage.instances if "Creative" in (m.content or "")]  # type: ignore[attr-defined]
    assert len(cards) == 1


async def test_tool_error_does_not_emit_card(tracing):
    _FakeMessage.instances.clear()  # type: ignore[attr-defined]
    async with tracing.TraceSession(user_session_id="s", prompt="go") as trace:
        tid = "toolu_fail"
        await trace.ingest(
            _assistant(
                text="Scraping",
                tool_uses=[
                    {
                        "id": tid,
                        "name": "mcp__adpipeline__scrape_url",
                        "input": {"url": "https://x"},
                    }
                ],
            )
        )
        await trace.ingest(_user_tool_result(tid, "timeout", is_error=True))
        await trace.ingest(_result(is_error=True))

    assert _FakeMessage.instances == []  # type: ignore[attr-defined]


async def test_chainlit_step_tree_mirrors_trace(tracing):
    async with tracing.TraceSession(user_session_id="s", prompt="go") as trace:
        agent_call_id = "toolu_agent_1"
        tool_call_id = "toolu_render_1"
        await trace.ingest(
            _assistant(
                text="Delegating",
                tool_uses=[
                    {
                        "id": agent_call_id,
                        "name": "Agent",
                        "input": {
                            "subagent_type": "creative-director",
                            "description": "Render 1 variant",
                        },
                    }
                ],
            )
        )
        await trace.ingest(
            _assistant(
                text="Rendering",
                parent_tool_use_id=agent_call_id,
                tool_uses=[
                    {
                        "id": tool_call_id,
                        "name": "mcp__adpipeline__render_creative",
                        "input": {"html": "<x/>", "variant_note": "A"},
                    }
                ],
            )
        )
        await trace.ingest(
            _user_tool_result(tool_call_id, [{"type": "text", "text": "ok"}])
        )
        await trace.ingest(
            _user_tool_result(agent_call_id, [{"type": "text", "text": "done"}])
        )
        await trace.ingest(_result())

    steps = _FakeStep.instances  # type: ignore[attr-defined]
    by_name = {s.name: s for s in steps}
    # No root coordinator step - subagent card is top-level, render tool
    # nests under it.
    assert "Campaign Coordinator" not in by_name
    assert "Creative Director" in by_name
    assert "Render creative" in by_name
    # The subagent step has no parent (it's top-level).
    assert by_name["Creative Director"].parent_id is None
    # Nested tool step parents under the subagent.
    assert by_name["Render creative"].parent_id == by_name["Creative Director"].id
