from urllib.error import URLError
from unittest.mock import patch

import pytest

from tools.view_reference import _view_brand_reference_handler


class _FakeResponse:
    def __init__(self, content_type: str):
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_view_brand_reference_accepts_common_image_extensions_without_head():
    with patch("tools.view_reference.urlopen", side_effect=AssertionError("HEAD should not run")):
        result = await _view_brand_reference_handler(
            {"url": "https://cdn.example.com/assets/hero.png"}
        )

    assert result == {
        "content": [
            {
                "type": "image",
                "source": {"type": "url", "url": "https://cdn.example.com/assets/hero.png"},
            }
        ]
    }


@pytest.mark.asyncio
async def test_view_brand_reference_rejects_non_https_urls():
    for url in ("http://cdn.example.com/hero.png", "file:///tmp/hero.png"):
        result = await _view_brand_reference_handler({"url": url})
        assert result["content"][0]["type"] == "text"
        assert "https" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_view_brand_reference_accepts_extensionless_image_url_via_head():
    with patch("tools.view_reference.urlopen", return_value=_FakeResponse("image/png")):
        result = await _view_brand_reference_handler(
            {"url": "https://cdn.example.com/assets/hero"}
        )

    assert result == {
        "content": [
            {
                "type": "image",
                "source": {"type": "url", "url": "https://cdn.example.com/assets/hero"},
            }
        ]
    }


@pytest.mark.asyncio
async def test_view_brand_reference_rejects_non_image_head_response():
    with patch("tools.view_reference.urlopen", return_value=_FakeResponse("text/html")):
        result = await _view_brand_reference_handler(
            {"url": "https://cdn.example.com/assets/hero"}
        )

    assert result["content"][0]["type"] == "text"
    assert "Content-Type text/html is not an image" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_view_brand_reference_returns_error_text_on_head_failure():
    with patch("tools.view_reference.urlopen", side_effect=URLError("timed out")):
        result = await _view_brand_reference_handler(
            {"url": "https://cdn.example.com/assets/hero"}
        )

    assert result["content"][0]["type"] == "text"
    assert "timed out" in result["content"][0]["text"]
