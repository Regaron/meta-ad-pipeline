"""Chainlit entrypoint for the Meta ad pipeline."""

import os

import chainlit as cl
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    query,
)
from dotenv import load_dotenv

from agents import COORDINATOR_PROMPT, build_agents
from tools.mcp_server import (
    RENDER_CREATIVE_TOOL,
    SCRAPE_URL_TOOL,
    VIEW_BRAND_REFERENCE_TOOL,
    adpipeline_server,
)
from tools.tracing import TraceSession

load_dotenv()

MODEL = "claude-opus-4-7"
_SDK_SESSION_KEY = "sdk_session_id"


async def _allow_all_tools(
    tool_name: str,
    tool_input: dict,
    context: ToolPermissionContext,
) -> PermissionResultAllow:
    """Blanket approve every tool call. Chainlit has no interactive approval
    UI, so the coordinator and all subagents must run fully permissive."""
    del tool_name, tool_input, context
    return PermissionResultAllow()


def build_options(resume_session_id: str | None) -> ClaudeAgentOptions:
    pipeboard_token = os.environ["PIPEBOARD_OAUTH_TOKEN"]
    return ClaudeAgentOptions(
        system_prompt=COORDINATOR_PROMPT,
        model=MODEL,
        # Keep the coordinator's direct toolbox narrow but include all the
        # adpipeline tools so it can hand-hold variant renders if a subagent
        # stalls. Actual delegation still flows through the Agent tool.
        allowed_tools=[
            "Agent",
            SCRAPE_URL_TOOL,
            RENDER_CREATIVE_TOOL,
            VIEW_BRAND_REFERENCE_TOOL,
        ],
        # Chainlit has no interactive approval UI. Bypass plus an allow-all
        # callback guarantees every MCP tool call - for the coordinator and
        # every subagent - is auto-approved.
        permission_mode="bypassPermissions",
        can_use_tool=_allow_all_tools,
        agents=build_agents(),
        mcp_servers={
            "adpipeline": adpipeline_server,
            "pipeboard": {
                "type": "http",
                "url": "https://meta-ads.mcp.pipeboard.co/",
                "headers": {"Authorization": f"Bearer {pipeboard_token}"},
            },
        },
        resume=resume_session_id,
    )


@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set(_SDK_SESSION_KEY, None)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    resume_session_id: str | None = cl.user_session.get(_SDK_SESSION_KEY)
    options = build_options(resume_session_id)

    assistant_msg: cl.Message | None = None

    # Stable session identifier for Langfuse trace grouping. Chainlit's
    # user_session.get("id") is a UUID that persists across the chat.
    chainlit_session_id = cl.user_session.get("id") or "anonymous"

    async with TraceSession(
        user_session_id=str(chainlit_session_id),
        prompt=message.content,
    ) as trace:
        async for event in query(prompt=message.content, options=options):
            await trace.ingest(event)

            if isinstance(event, ResultMessage):
                if event.session_id:
                    cl.user_session.set(_SDK_SESSION_KEY, event.session_id)
                continue

            if isinstance(event, SystemMessage):
                continue

            if not isinstance(event, AssistantMessage):
                continue

            # Subagent text is streamed into their own cl.Step cards by the
            # tracer; only the coordinator's final-answer text reaches the
            # top-level chat bubble.
            if event.parent_tool_use_id:
                continue

            for block in event.content:
                if not isinstance(block, TextBlock):
                    continue
                if assistant_msg is None:
                    assistant_msg = await cl.Message(content="").send()
                await assistant_msg.stream_token(block.text)

    if assistant_msg is not None:
        await assistant_msg.update()
