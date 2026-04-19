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


def build_options(resume_session_id: str | None) -> ClaudeAgentOptions:
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
    options = build_options(resume_session_id)

    assistant_msg: cl.Message | None = None
    current_subagent_step: cl.Step | None = None
    current_parent_tool_use_id: str | None = None
    seen_image_urls: set[str] = set()

    async for event in query(prompt=message.content, options=options):
        if isinstance(event, ResultMessage):
            if event.session_id:
                cl.user_session.set(_SDK_SESSION_KEY, event.session_id)
            continue

        if isinstance(event, SystemMessage):
            continue

        if not isinstance(event, AssistantMessage):
            continue

        parent_id = getattr(event, "parent_tool_use_id", None)
        if parent_id != current_parent_tool_use_id and current_subagent_step is not None:
            await current_subagent_step.__aexit__(None, None, None)
            current_subagent_step = None
            current_parent_tool_use_id = None

        if parent_id and current_subagent_step is None:
            current_subagent_step = cl.Step(name="subagent", type="run")
            await current_subagent_step.__aenter__()
            current_parent_tool_use_id = parent_id

        for block in event.content:
            if not isinstance(block, TextBlock):
                continue

            if parent_id and current_subagent_step is not None:
                current_subagent_step.output = (
                    (current_subagent_step.output or "") + block.text
                )
                await current_subagent_step.update()
            else:
                if assistant_msg is None:
                    assistant_msg = await cl.Message(content="").send()
                await assistant_msg.stream_token(block.text)

            for url in _extract_tigris_urls(block.text):
                if url in seen_image_urls:
                    continue
                seen_image_urls.add(url)
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

    public_host = re.escape(os.environ.get("TIGRIS_PUBLIC_HOST", "t3.tigrisfiles.io"))
    pattern = rf"https://[A-Za-z0-9_.-]+\.{public_host}/creatives/[A-Za-z0-9_-]+\.png"
    return list(dict.fromkeys(re.findall(pattern, text)))
