import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, query


@dataclass(frozen=True)
class LiveSettings:
    browser_use_api_key: str
    pipeboard_oauth_token: str
    tigris_storage_access_key_id: str
    tigris_storage_secret_access_key: str
    tigris_storage_bucket: str
    tigris_api_endpoint: str
    tigris_public_host: str
    test_landing_url: str
    test_meta_ad_account_id: str
    test_meta_page_id: str
    test_allow_activation: bool


@dataclass(frozen=True)
class QueryCapture:
    session_id: str
    assistant_text: str
    agent_tool_use_ids: dict[str, list[str]]
    subagent_text_by_parent: dict[str, str]


@dataclass(frozen=True)
class LiveBuildResult:
    session_id: str
    creative_payload: list[dict[str, Any]]
    media_buyer_payload: dict[str, Any]
    creative_agent_invocations: int
    media_buyer_invocations: int
    campaign_id: str
    adset_id: str
    creative_ids: list[str]
    ad_ids: list[str]
    status: str | None
    objective: str | None
    daily_budget_cents: int | None
    targeting_summary: str | None
    budget_cap_applied: bool | None
    final_text: str


def _claude_auth_available() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True

    if shutil.which("claude") is None:
        return False

    try:
        result = subprocess.run(
            ["claude", "auth", "status", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return False
    if result.returncode != 0:
        return False

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False

    return bool(payload.get("loggedIn"))


def _load_live_settings() -> LiveSettings:
    required = {
        "BROWSER_USE_API_KEY": os.environ.get("BROWSER_USE_API_KEY"),
        "PIPEBOARD_OAUTH_TOKEN": os.environ.get("PIPEBOARD_OAUTH_TOKEN"),
        "TIGRIS_STORAGE_ACCESS_KEY_ID": os.environ.get(
            "TIGRIS_STORAGE_ACCESS_KEY_ID"
        ),
        "TIGRIS_STORAGE_SECRET_ACCESS_KEY": os.environ.get(
            "TIGRIS_STORAGE_SECRET_ACCESS_KEY"
        ),
        "TIGRIS_STORAGE_BUCKET": os.environ.get("TIGRIS_STORAGE_BUCKET"),
        "TEST_LANDING_URL": os.environ.get("TEST_LANDING_URL"),
        "TEST_META_AD_ACCOUNT_ID": os.environ.get("TEST_META_AD_ACCOUNT_ID"),
        "TEST_META_PAGE_ID": os.environ.get("TEST_META_PAGE_ID"),
    }
    missing = [name for name, value in required.items() if not value]
    if not _claude_auth_available():
        missing.append("ANTHROPIC_API_KEY or a local `claude auth login` session")

    if missing:
        raise RuntimeError(
            "integration_live environment is incomplete:\n"
            + "\n".join(f"- {item}" for item in missing)
        )

    return LiveSettings(
        browser_use_api_key=required["BROWSER_USE_API_KEY"],
        pipeboard_oauth_token=required["PIPEBOARD_OAUTH_TOKEN"],
        tigris_storage_access_key_id=required["TIGRIS_STORAGE_ACCESS_KEY_ID"],
        tigris_storage_secret_access_key=required[
            "TIGRIS_STORAGE_SECRET_ACCESS_KEY"
        ],
        tigris_storage_bucket=required["TIGRIS_STORAGE_BUCKET"],
        tigris_api_endpoint=os.environ.get("TIGRIS_API_ENDPOINT", "https://t3.storage.dev"),
        tigris_public_host=os.environ.get("TIGRIS_PUBLIC_HOST", "t3.tigrisfiles.io"),
        test_landing_url=required["TEST_LANDING_URL"],
        test_meta_ad_account_id=required["TEST_META_AD_ACCOUNT_ID"],
        test_meta_page_id=required["TEST_META_PAGE_ID"],
        test_allow_activation=os.environ.get("TEST_ALLOW_ACTIVATION") == "1",
    )


def _load_json_blob(text: str) -> Any:
    stripped = text.strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            payload, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if not stripped[index + end :].strip():
            return payload
    raise AssertionError(f"Expected JSON payload in text:\n{stripped}")


async def _run_query_capture_async(prompt: str, options) -> QueryCapture:
    session_id: str | None = None
    assistant_parts: list[str] = []
    agent_tool_use_ids: dict[str, list[str]] = {
        "creative-director": [],
        "media-buyer": [],
    }
    subagent_parts: dict[str, list[str]] = {}

    async for event in query(prompt=prompt, options=options):
        if isinstance(event, ResultMessage):
            session_id = event.session_id or session_id
            continue

        if not isinstance(event, AssistantMessage):
            continue

        parent_id = event.parent_tool_use_id
        for block in event.content:
            if isinstance(block, ToolUseBlock) and block.name == "Agent":
                raw_input = json.dumps(block.input, sort_keys=True)
                for agent_name in agent_tool_use_ids:
                    if agent_name in raw_input:
                        agent_tool_use_ids[agent_name].append(block.id)
            elif isinstance(block, TextBlock):
                if parent_id:
                    subagent_parts.setdefault(parent_id, []).append(block.text)
                else:
                    assistant_parts.append(block.text)

    if session_id is None:
        raise AssertionError("Expected a ResultMessage carrying a session_id.")

    return QueryCapture(
        session_id=session_id,
        assistant_text="".join(assistant_parts).strip(),
        agent_tool_use_ids=agent_tool_use_ids,
        subagent_text_by_parent={
            parent_id: "".join(parts).strip()
            for parent_id, parts in subagent_parts.items()
        },
    )


@pytest.fixture(scope="session")
def live_settings() -> LiveSettings:
    return _load_live_settings()


@pytest.fixture(scope="session")
def test_run_label() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"codex-it-{stamp}"


@pytest.fixture(scope="session")
def coordinator_options(live_settings: LiveSettings):
    from app import build_options

    return build_options(None)


@pytest.fixture(scope="session")
def run_query_capture(live_settings: LiveSettings):
    from app import build_options

    def _runner(prompt: str, resume_session_id: str | None = None) -> QueryCapture:
        return asyncio.run(_run_query_capture_async(prompt, build_options(resume_session_id)))

    return _runner


@pytest.fixture(scope="module")
def live_build_result(
    live_settings: LiveSettings,
    test_run_label: str,
    coordinator_options,
    run_query_capture,
) -> LiveBuildResult:
    _ = coordinator_options
    prompt = (
        f"Build a paused sandbox integration test ad for {live_settings.test_landing_url}, "
        f"ad account {live_settings.test_meta_ad_account_id}, "
        f"page {live_settings.test_meta_page_id}, "
        f"2 variants, label {test_run_label}."
    )
    capture = run_query_capture(prompt)

    creative_parent_ids = capture.agent_tool_use_ids["creative-director"]
    media_parent_ids = capture.agent_tool_use_ids["media-buyer"]

    creative_text = "\n".join(
        capture.subagent_text_by_parent[parent_id]
        for parent_id in creative_parent_ids
        if capture.subagent_text_by_parent.get(parent_id)
    )
    media_text = "\n".join(
        capture.subagent_text_by_parent[parent_id]
        for parent_id in media_parent_ids
        if capture.subagent_text_by_parent.get(parent_id)
    )

    creative_payload = _load_json_blob(creative_text)
    media_payload = _load_json_blob(media_text)

    if not isinstance(creative_payload, list):
        raise AssertionError("creative-director output must parse as a JSON array.")
    if not isinstance(media_payload, dict):
        raise AssertionError("media-buyer output must parse as a JSON object.")

    return LiveBuildResult(
        session_id=capture.session_id,
        creative_payload=creative_payload,
        media_buyer_payload=media_payload,
        creative_agent_invocations=len(creative_parent_ids),
        media_buyer_invocations=len(media_parent_ids),
        campaign_id=media_payload["campaign_id"],
        adset_id=media_payload["adset_id"],
        creative_ids=list(media_payload["creative_ids"]),
        ad_ids=list(media_payload["ad_ids"]),
        status=media_payload.get("status"),
        objective=media_payload.get("objective"),
        daily_budget_cents=media_payload.get("daily_budget_cents"),
        targeting_summary=media_payload.get("targeting_summary"),
        budget_cap_applied=media_payload.get("budget_cap_applied"),
        final_text=capture.assistant_text,
    )
