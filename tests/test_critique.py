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
async def test_critique_returns_neutral_verdict_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = await _critique_handler(
        {"png_url": "https://x/creatives/a.png", "variant_note": "A"}
    )

    assert result["content"][0]["type"] == "text"
    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "ok"
    assert payload["issues"] == []
    assert "ANTHROPIC_API_KEY" in payload["skipped_reason"]


@pytest.mark.asyncio
async def test_critique_parses_json_verdict_from_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response(
        json.dumps(
            {
                "verdict": "iterate",
                "issues": [
                    {"area": "headline", "severity": "block", "detail": "wraps per-word"}
                ],
                "strengths": ["palette is cohesive"],
            }
        )
    )

    with patch("tools.critique._get_client", return_value=fake_client):
        result = await _critique_handler(
            {"png_url": "https://cdn/x.png", "variant_note": "v1"}
        )

    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "iterate"
    assert payload["issues"][0]["area"] == "headline"
    assert payload["strengths"] == ["palette is cohesive"]

    # The Anthropic call was made with an image block carrying the url.
    call = fake_client.messages.create.call_args
    user_content = call.kwargs["messages"][0]["content"]
    image_block = next(b for b in user_content if b["type"] == "image")
    assert image_block["source"]["type"] == "url"
    assert image_block["source"]["url"] == "https://cdn/x.png"


@pytest.mark.asyncio
async def test_critique_strips_code_fences_from_model_output(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    fenced = '```json\n{"verdict": "ok", "issues": [], "strengths": []}\n```'
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response(fenced)

    with patch("tools.critique._get_client", return_value=fake_client):
        result = await _critique_handler({"png_url": "https://x/y.png", "variant_note": ""})

    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "ok"


@pytest.mark.asyncio
async def test_critique_falls_back_when_model_returns_garbage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_response("not json at all")

    with patch("tools.critique._get_client", return_value=fake_client):
        result = await _critique_handler({"png_url": "https://x/y.png", "variant_note": ""})

    payload = json.loads(result["content"][0]["text"])
    # Falls back to ok so the pipeline isn't blocked by a flaky critique.
    assert payload["verdict"] == "ok"
    assert "non-JSON" in payload["skipped_reason"]


@pytest.mark.asyncio
async def test_critique_falls_back_on_api_exception(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("rate limited")

    with patch("tools.critique._get_client", return_value=fake_client):
        result = await _critique_handler({"png_url": "https://x/y.png", "variant_note": ""})

    payload = json.loads(result["content"][0]["text"])
    assert payload["verdict"] == "ok"
    assert "rate limited" in payload["skipped_reason"]
