"""Tests for tools.critique.critique_render."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.critique import _critique_handler


def _anthropic_response(text: str):
    """Build a fake Anthropic messages.create() response whose .content list
    contains a single text block with `text`."""
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block])


@pytest.mark.asyncio
async def test_critique_uses_claude_code_first(monkeypatch):
    """Primary path: claude-agent-sdk query() with the image in the user
    message. Should succeed even when ANTHROPIC_API_KEY is absent."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def fake_cc(png_url, prompt_text):
        assert png_url == "https://cdn/x.png"
        return (
            json.dumps({"verdict": "ok", "issues": [], "strengths": ["clear"]}),
            None,
        )

    with patch("tools.critique._critique_via_claude_code", side_effect=fake_cc):
        result = await _critique_handler(
            {"png_url": "https://cdn/x.png", "variant_note": "v1"}
        )

    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "ok"
    assert payload["strengths"] == ["clear"]
    assert "skipped_reason" not in payload


@pytest.mark.asyncio
async def test_critique_falls_back_to_anthropic_api_when_claude_code_unavailable(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    async def failing_cc(png_url, prompt_text):
        return None, "claude-code not available"

    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response(
        json.dumps(
            {
                "verdict": "iterate",
                "issues": [{"area": "headline", "severity": "block", "detail": "wraps"}],
                "strengths": [],
            }
        )
    )

    with (
        patch("tools.critique._critique_via_claude_code", side_effect=failing_cc),
        patch("tools.critique._get_client", return_value=fake_client),
    ):
        result = await _critique_handler(
            {"png_url": "https://cdn/x.png", "variant_note": "v1"}
        )

    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "iterate"
    assert payload["issues"][0]["area"] == "headline"

    # Confirm Anthropic API got the image URL.
    call = fake_client.messages.create.call_args
    user_content = call.kwargs["messages"][0]["content"]
    image_block = next(b for b in user_content if b["type"] == "image")
    assert image_block["source"]["url"] == "https://cdn/x.png"


@pytest.mark.asyncio
async def test_critique_neutral_when_both_paths_fail(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def failing_cc(png_url, prompt_text):
        return None, "cli not logged in"

    with patch("tools.critique._critique_via_claude_code", side_effect=failing_cc):
        result = await _critique_handler(
            {"png_url": "https://cdn/x.png", "variant_note": "v1"}
        )

    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "ok"  # fail-open so the pipeline doesn't block.
    assert "cli not logged in" in payload["skipped_reason"]
    assert "ANTHROPIC_API_KEY" in payload["skipped_reason"]


@pytest.mark.asyncio
async def test_critique_strips_code_fences_from_model_output(monkeypatch):
    fenced = '```json\n{"verdict": "ok", "issues": [], "strengths": []}\n```'

    async def fake_cc(png_url, prompt_text):
        return fenced, None

    with patch("tools.critique._critique_via_claude_code", side_effect=fake_cc):
        result = await _critique_handler({"png_url": "https://x/y.png", "variant_note": ""})

    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "ok"


@pytest.mark.asyncio
async def test_critique_falls_back_when_model_returns_garbage(monkeypatch):
    async def fake_cc(png_url, prompt_text):
        return "not json at all", None

    with patch("tools.critique._critique_via_claude_code", side_effect=fake_cc):
        result = await _critique_handler({"png_url": "https://x/y.png", "variant_note": ""})

    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "ok"
    assert "non-JSON" in payload["skipped_reason"]


@pytest.mark.asyncio
async def test_critique_falls_back_when_verdict_missing(monkeypatch):
    async def fake_cc(png_url, prompt_text):
        return json.dumps({"issues": []}), None  # no `verdict`

    with patch("tools.critique._critique_via_claude_code", side_effect=fake_cc):
        result = await _critique_handler({"png_url": "https://x/y.png", "variant_note": ""})

    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "ok"
    assert "missing 'verdict'" in payload["skipped_reason"]
