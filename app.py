"""Chainlit entrypoint for the Meta ad pipeline."""
import os
import re

import chainlit as cl
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    query,
)
from dotenv import load_dotenv

from agents import COORDINATOR_PROMPT, build_agents
from tools.mcp_server import SCRAPE_URL_TOOL, adpipeline_server

load_dotenv()

MODEL = "claude-opus-4-7"
_SDK_SESSION_KEY = "sdk_session_id"


def _build_options(resume_session_id: str | None) -> ClaudeAgentOptions:
    pipeboard_token = os.environ["PIPEBOARD_OAUTH_TOKEN"]
    return ClaudeAgentOptions(
        system_prompt=COORDINATOR_PROMPT,
        model=MODEL,
        allowed_tools=["Agent", SCRAPE_URL_TOOL],
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
    options = _build_options(resume_session_id)

    assistant_msg: cl.Message | None = None
    current_subagent_step: cl.Step | None = None
    seen_tool_use_ids: set[str] = set()

    async for event in query(prompt=message.content, options=options):
        # Capture SDK session id on first ResultMessage so future turns resume.
        if isinstance(event, ResultMessage):
            if event.session_id:
                cl.user_session.set(_SDK_SESSION_KEY, event.session_id)
            continue

        if isinstance(event, SystemMessage):
            continue

        if not isinstance(event, AssistantMessage):
            continue

        # Detect subagent context via parent_tool_use_id — show as a nested step.
        parent_id = getattr(event, "parent_tool_use_id", None)
        if parent_id and parent_id not in seen_tool_use_ids:
            seen_tool_use_ids.add(parent_id)
            current_subagent_step = cl.Step(name="subagent", type="run")
            await current_subagent_step.__aenter__()

        for block in event.content:
            if isinstance(block, TextBlock):
                if parent_id and current_subagent_step is not None:
                    # Subagent chatter -> step output
                    current_subagent_step.output = (
                        (current_subagent_step.output or "") + block.text
                    )
                    await current_subagent_step.update()
                else:
                    # Coordinator output -> main message stream
                    if assistant_msg is None:
                        assistant_msg = await cl.Message(content="").send()
                    await assistant_msg.stream_token(block.text)

                # Detect PNG URLs emitted by the creative-director and display inline.
                text = getattr(block, "text", "") or ""
                for url in _extract_tigris_urls(text):
                    await cl.Message(
                        content=f"Creative: {url}",
                        elements=[
                            cl.Image(
                                url=url,
                                name=url.rsplit("/", 1)[-1],
                                display="inline",
                            )
                        ],
                    ).send()

    if current_subagent_step is not None:
        await current_subagent_step.__aexit__(None, None, None)

    if assistant_msg is not None:
        await assistant_msg.update()


def _extract_tigris_urls(text: str) -> list[str]:
    """Find Tigris public PNG URLs in streamed text so the UI can preview them."""

    pattern = r"https://[a-zA-Z0-9_\-\.]+\.t3\.storage\.dev/creatives/[a-f0-9]+\.png"
    return list(dict.fromkeys(re.findall(pattern, text)))  # preserve order, dedupe
