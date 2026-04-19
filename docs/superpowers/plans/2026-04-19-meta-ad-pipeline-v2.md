# Meta Ad Pipeline v2 Implementation Plan (delta)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the in-progress meta-ad-pipeline worktree in line with the v2 design (`docs/superpowers/specs/2026-04-19-meta-ad-pipeline-design-v2.md`): replace `AdCopy` with `BrandResearch`, switch Tigris credentials to the `TIGRIS_STORAGE_*` namespace with the `t3.tigrisfiles.io` CDN host, add the `view_brand_reference` vision tool, and wire up the agentic coordinator + 2 subagents + Chainlit app with no baked Meta-ad defaults.

**Architecture:** One Chainlit Python process. Coordinator (top-level `query()`) calls `scrape_url` directly; delegates design to `creative-director` (uses `render_creative` + `view_brand_reference` to compose synthetic HTML/CSS/SVG creatives at 1080×1080 informed by scraped reference photos); delegates publishing/analytics to `media-buyer` (composes Meta campaign objective, optimization, budget, and targeting from `BrandResearch` per request, with a $50/day budget cap unless the user explicitly overrides). Tigris uploads via `boto3` against the S3-compatible API endpoint, public URLs synthesized against the CDN host.

**Tech Stack:** Python 3.11+, `claude-agent-sdk>=0.2.111`, `browser-use-sdk>=3.0`, `weasyprint`, `pypdfium2`, `pillow`, `boto3`, `chainlit>=2.0`, `pydantic>=2.9`, `python-dotenv`, `uv` for package management.

**Spec:** `docs/superpowers/specs/2026-04-19-meta-ad-pipeline-design-v2.md` (v2). Layered on `2026-04-19-meta-ad-pipeline-design.md` (v1).

**Worktree state at plan-write time:** v1 plan tasks 1–6 already committed (`dddc103`, `662b88e`, `6f3d03c`, `a61b4d2` + `7558e8b`, `9bbc665` + `bd5c9a7` + `4f00f0a`, `eb8cb0c`). v2 amends those files in fresh commits and adds the rest. If parallel implementation lands more v1 tasks before this plan executes, re-check the disposition in the spec's §9 and skip any task whose work is already in place.

---

## Task 0: Confirm green baseline

**Files:** none modified.

- [ ] **Step 1: Verify the worktree is on `meta-ad-pipeline`**

Run: `git branch --show-current`
Expected output: `meta-ad-pipeline`

- [ ] **Step 2: Run the existing test suite to confirm it passes**

Run: `uv run pytest tests/ -v`
Expected: all currently-committed tests pass (3 in `test_schemas.py`, 1 in `test_scrape.py`, 1 in `test_render.py`). If anything fails, fix the underlying break before continuing — v2 amendments build on a green baseline.

- [ ] **Step 3: Note the starting HEAD for rollback safety**

Run: `git rev-parse HEAD`
Expected: a commit hash. Record it; if a v2 task goes wrong, `git reset --hard <hash>` returns to the baseline.

---

## Task 1: Amend `.env.example` for the new Tigris namespace

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Replace the Tigris block with the v2 env vars**

Edit `.env.example`. Find the section starting with `# Tigris (S3-compatible object storage)` and replace from that comment through `TIGRIS_BUCKET=ad-pipeline-creatives` (inclusive) with:

```bash
# Tigris (S3-compatible object storage)
# Get credentials at https://console.tigris.dev
TIGRIS_STORAGE_ACCESS_KEY_ID=tid_...
TIGRIS_STORAGE_SECRET_ACCESS_KEY=tsec_...
TIGRIS_STORAGE_BUCKET=ad-images-tigris

# Optional Tigris overrides — defaults shown.
# TIGRIS_API_ENDPOINT=https://t3.storage.dev
# TIGRIS_PUBLIC_HOST=t3.tigrisfiles.io
```

The `ANTHROPIC_API_KEY`, `BROWSER_USE_API_KEY`, and `PIPEBOARD_OAUTH_TOKEN` blocks above the Tigris block stay unchanged.

- [ ] **Step 2: Verify the file**

Run: `cat .env.example`
Expected: contains `TIGRIS_STORAGE_ACCESS_KEY_ID`, `TIGRIS_STORAGE_SECRET_ACCESS_KEY`, `TIGRIS_STORAGE_BUCKET=ad-images-tigris`. Does NOT contain `AWS_ACCESS_KEY_ID`, `AWS_ENDPOINT_URL`, `AWS_REGION`, `TIGRIS_BUCKET`.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "chore(env): switch .env.example to TIGRIS_STORAGE_* namespace"
```

---

## Task 2: Replace `AdCopy` with `BrandResearch` in schemas

**Files:**
- Modify: `tools/schemas.py`
- Modify: `tests/test_schemas.py`

- [ ] **Step 1: Rewrite `tests/test_schemas.py` to target the new schema**

Replace the entire contents of `tests/test_schemas.py` with:

```python
import pydantic
import pytest

from tools.schemas import (
    BrandIdentity,
    BrandResearch,
    CoreValueProp,
    CreativeCopyIdea,
    RenderedCreative,
)


_SAMPLE_RESEARCH = {
    "source_url": "https://acme.com",
    "identity": {
        "logo_url": "https://acme.com/logo.svg",
        "primary_color_hexes": ["#0F62FE", "#161616"],
    },
    "value_prop": {
        "headline": "Premium widgets engineered to last.",
        "top_3_benefits": [
            "Free shipping",
            "Lifetime warranty",
            "30-day returns",
        ],
    },
    "visual_asset_urls": [
        "https://acme.com/img/hero.jpg",
        "https://acme.com/img/lifestyle1.jpg",
    ],
    "tone_adjectives": ["confident", "warm", "playful"],
    "cta_button_text": "Get Started",
    "creative_copy_idea": {
        "hook": "Tired of widgets that break?",
        "body": "Our widgets are built from aerospace-grade materials.",
        "headline": "Widgets That Outlast You",
    },
}


def test_brand_research_validates_full_payload():
    br = BrandResearch.model_validate(_SAMPLE_RESEARCH)
    assert br.source_url == "https://acme.com"
    assert br.identity.logo_url == "https://acme.com/logo.svg"
    assert br.identity.primary_color_hexes == ["#0F62FE", "#161616"]
    assert br.value_prop.headline.startswith("Premium")
    assert len(br.value_prop.top_3_benefits) == 3
    assert len(br.visual_asset_urls) == 2
    assert br.tone_adjectives == ["confident", "warm", "playful"]
    assert br.cta_button_text == "Get Started"
    assert br.creative_copy_idea.headline == "Widgets That Outlast You"


def test_brand_research_allows_null_logo_and_empty_assets():
    payload = dict(_SAMPLE_RESEARCH)
    payload["identity"] = {"logo_url": None, "primary_color_hexes": ["#000000"]}
    payload["visual_asset_urls"] = []
    br = BrandResearch.model_validate(payload)
    assert br.identity.logo_url is None
    assert br.visual_asset_urls == []


def test_tone_adjectives_must_be_exactly_three():
    payload = dict(_SAMPLE_RESEARCH)
    payload["tone_adjectives"] = ["confident", "warm"]
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)
    payload["tone_adjectives"] = ["a", "b", "c", "d"]
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)


def test_top_3_benefits_must_be_exactly_three():
    payload = dict(_SAMPLE_RESEARCH)
    payload["value_prop"] = {
        "headline": "x",
        "top_3_benefits": ["just", "two"],
    }
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)


def test_visual_assets_capped_at_three():
    payload = dict(_SAMPLE_RESEARCH)
    payload["visual_asset_urls"] = [f"https://x/{i}" for i in range(4)]
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)


def test_primary_color_hexes_must_match_pattern():
    payload = dict(_SAMPLE_RESEARCH)
    payload["identity"] = {
        "logo_url": None,
        "primary_color_hexes": ["not-a-hex"],
    }
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)


def test_primary_color_hexes_min_one_max_three():
    payload = dict(_SAMPLE_RESEARCH)
    payload["identity"] = {"logo_url": None, "primary_color_hexes": []}
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)
    payload["identity"] = {
        "logo_url": None,
        "primary_color_hexes": ["#000", "#fff", "#abc", "#def"],
    }
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)


def test_rendered_creative_roundtrip():
    rc = RenderedCreative(
        variant_id="abc123",
        variant_note="bold typographic",
        png_url="https://ad-images-tigris.t3.tigrisfiles.io/creatives/abc123.png",
    )
    assert rc.variant_id == "abc123"
    assert rc.png_url.startswith("https://")
```

- [ ] **Step 2: Run the tests to verify they all fail (BrandResearch doesn't exist yet)**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: collection error or `ImportError: cannot import name 'BrandResearch' from 'tools.schemas'`. (The `RenderedCreative` test also won't run because the import line fails.)

- [ ] **Step 3: Rewrite `tools/schemas.py`**

Replace the entire contents of `tools/schemas.py` with:

```python
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

HexColor = Annotated[
    str,
    StringConstraints(pattern=r"^#[0-9A-Fa-f]{3,8}$"),
]


class BrandIdentity(BaseModel):
    """Visual identity scraped from a landing page."""

    logo_url: str | None = Field(
        ...,
        description="Primary logo URL on the page; null if no usable logo found.",
    )
    primary_color_hexes: list[HexColor] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="1-3 hex codes for the brand's primary colors, e.g. '#0F62FE'.",
    )


class CoreValueProp(BaseModel):
    """The page's headline value proposition."""

    headline: str = Field(..., description="The site's main headline, verbatim.")
    top_3_benefits: list[str] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exactly 3 short benefit statements.",
    )


class CreativeCopyIdea(BaseModel):
    """One Problem/Solution ad copy draft authored by the scraper."""

    hook: str = Field(..., description="A relatable pain point.")
    body: str = Field(..., description="How this product solves the pain point.")
    headline: str = Field(
        ...,
        description="Punchy, benefit-driven headline; target <=40 chars.",
    )


class BrandResearch(BaseModel):
    """Performance-marketing research extracted from a landing page."""

    source_url: str
    identity: BrandIdentity
    value_prop: CoreValueProp
    visual_asset_urls: list[str] = Field(
        ...,
        max_length=3,
        description="Up to 3 high-quality product/lifestyle image URLs; may be empty.",
    )
    tone_adjectives: list[str] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exactly 3 adjectives describing the brand's voice.",
    )
    cta_button_text: str = Field(
        ...,
        description='Literal primary CTA button text on the page, e.g. "Get Started".',
    )
    creative_copy_idea: CreativeCopyIdea


class RenderedCreative(BaseModel):
    """A rendered ad creative uploaded to Tigris."""

    variant_id: str
    variant_note: str
    png_url: str
```

- [ ] **Step 4: Run the schema tests to verify they pass**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/schemas.py tests/test_schemas.py
git commit -m "feat(schemas): replace AdCopy with BrandResearch family"
```

---

## Task 3: Update `scrape_url` to return `BrandResearch`

**Files:**
- Modify: `tools/scrape.py`
- Modify: `tests/test_scrape.py`

- [ ] **Step 1: Rewrite `tests/test_scrape.py` to expect BrandResearch**

Replace the entire contents of `tests/test_scrape.py` with:

```python
from unittest.mock import AsyncMock, patch

import pytest

from tools.schemas import BrandResearch


_CANNED_RESEARCH = BrandResearch.model_validate(
    {
        "source_url": "https://acme.com",
        "identity": {
            "logo_url": "https://acme.com/logo.svg",
            "primary_color_hexes": ["#0F62FE", "#161616"],
        },
        "value_prop": {
            "headline": "Premium widgets engineered to last.",
            "top_3_benefits": [
                "Free shipping",
                "Lifetime warranty",
                "30-day returns",
            ],
        },
        "visual_asset_urls": [
            "https://acme.com/img/hero.jpg",
            "https://acme.com/img/lifestyle1.jpg",
        ],
        "tone_adjectives": ["confident", "warm", "playful"],
        "cta_button_text": "Get Started",
        "creative_copy_idea": {
            "hook": "Tired of widgets that break?",
            "body": "Our widgets are built from aerospace-grade materials.",
            "headline": "Widgets That Outlast You",
        },
    }
)


@pytest.mark.asyncio
async def test_scrape_url_returns_text_block_with_brand_research_json():
    """scrape_url wraps browser-use-sdk and returns BrandResearch as JSON text."""
    fake_result = AsyncMock()
    fake_result.output = _CANNED_RESEARCH

    fake_client = AsyncMock()
    fake_client.run = AsyncMock(return_value=fake_result)

    with patch("tools.scrape.AsyncBrowserUse", return_value=fake_client):
        from tools.scrape import _scrape_handler

        result = await _scrape_handler(
            {
                "url": "https://acme.com",
                "extraction_goal": "Focus on the B2B persona.",
            }
        )

    assert "content" in result
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block["type"] == "text"

    parsed = BrandResearch.model_validate_json(block["text"])
    assert parsed.identity.primary_color_hexes == ["#0F62FE", "#161616"]
    assert parsed.creative_copy_idea.headline == "Widgets That Outlast You"
    assert parsed.cta_button_text == "Get Started"

    fake_client.run.assert_awaited_once()
    call_kwargs = fake_client.run.await_args.kwargs
    assert "https://acme.com" in call_kwargs["task"]
    assert "performance marketing researcher" in call_kwargs["task"]
    assert "Problem/Solution" in call_kwargs["task"]
    assert "Focus on the B2B persona." in call_kwargs["task"]
    assert call_kwargs["output_schema"] is BrandResearch
    assert call_kwargs["model"] == "claude-opus-4.6"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_scrape.py -v`
Expected: FAIL — assertions on `"performance marketing researcher"` / `"Problem/Solution"` won't match the v1 prompt; `output_schema is BrandResearch` won't hold.

- [ ] **Step 3: Rewrite `tools/scrape.py`**

Replace the entire contents of `tools/scrape.py` with:

```python
from typing import Any

from browser_use_sdk.v3 import AsyncBrowserUse
from claude_agent_sdk import tool

from tools.schemas import BrandResearch


_RESEARCH_TASK_TEMPLATE = (
    "Navigate to {url} and act as a performance marketing researcher.\n"
    "- Brand Identity: find the primary logo URL and 1-3 hex codes for the brand's primary colors.\n"
    "- Core Value Prop: extract the main headline and the top 3 benefits the product provides.\n"
    "- Visual Assets: collect URLs for up to 3 high-quality images (product or lifestyle) suitable for ads. "
    "Empty list is acceptable if none are usable.\n"
    "- Tone of Voice: describe the brand's writing style in exactly 3 adjectives.\n"
    "- Call to Action: identify the primary button text on the page (e.g., \"Get Started\"). "
    "Do NOT translate it - return the literal text.\n"
    "- Creative Copy Idea: based on the site's content, write one Problem/Solution ad copy variant: "
    "Hook (a relatable pain point), Body (how this product solves it), Headline (punchy, benefit-driven, max 40 chars).\n"
    "Additional focus: {extraction_goal}\n"
    "Return all findings as a single BrandResearch JSON object."
)


async def _scrape_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Drive Browser Use Cloud to extract BrandResearch from a landing page.

    Exposed as a plain coroutine so tests can call it without depending on
    SdkMcpTool internals. The @tool-decorated `scrape_url` below wraps it
    for registration with create_sdk_mcp_server.
    """
    client = AsyncBrowserUse()
    task = _RESEARCH_TASK_TEMPLATE.format(
        url=args["url"],
        extraction_goal=args["extraction_goal"],
    )
    result = await client.run(
        task=task,
        model="claude-opus-4.6",
        output_schema=BrandResearch,
    )
    research: BrandResearch = result.output
    return {
        "content": [
            {"type": "text", "text": research.model_dump_json()}
        ]
    }


scrape_url = tool(
    "scrape_url",
    "Scrape a landing URL via Browser Use Cloud and return structured BrandResearch.",
    {"url": str, "extraction_goal": str},
)(_scrape_handler)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_scrape.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/scrape.py tests/test_scrape.py
git commit -m "feat(scrape): switch scrape_url to BrandResearch + research prompt"
```

---

## Task 4: Switch `creative.py` to TIGRIS_STORAGE_* env + CDN public URL

**Files:**
- Modify: `tools/creative.py`
- Modify: `tests/test_render.py`

- [ ] **Step 1: Rewrite `tests/test_render.py` to use the new env vars + URL host**

Replace the entire contents of `tests/test_render.py` with:

```python
import asyncio
import io
import json
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws
from PIL import Image

FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "sample_creative.html").read_text()


@pytest.fixture(autouse=True)
def _tigris_env(monkeypatch):
    monkeypatch.setenv("TIGRIS_STORAGE_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("TIGRIS_STORAGE_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("TIGRIS_STORAGE_BUCKET", "ad-images-tigris")
    monkeypatch.setenv("TIGRIS_API_ENDPOINT", "https://t3.storage.dev")
    monkeypatch.setenv("TIGRIS_PUBLIC_HOST", "t3.tigrisfiles.io")
    # Make sure no ambient AWS_* creds leak in.
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)


@mock_aws
def test_render_creative_produces_1080_png_and_uploads_to_tigris():
    """render_creative must: render PDF -> PNG, upload with public-read ACL,
    return URL on the Tigris CDN host."""

    async def _run() -> None:
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="ad-images-tigris")

        from tools.creative import _render_handler

        with patch("tools.creative._s3_client", return_value=s3):
            result = await _render_handler(
                {"html": FIXTURE_HTML, "variant_note": "bold gradient test"}
            )

        assert "content" in result
        payload = json.loads(result["content"][0]["text"])
        assert payload["variant_note"] == "bold gradient test"
        assert payload["png_url"].startswith(
            "https://ad-images-tigris.t3.tigrisfiles.io/creatives/"
        )
        assert payload["png_url"].endswith(".png")

        # The object exists in the mock bucket with correct Content-Type
        key = payload["png_url"].split(".t3.tigrisfiles.io/")[1]
        head = s3.head_object(Bucket="ad-images-tigris", Key=key)
        assert head["ContentType"] == "image/png"

        acl = s3.get_object_acl(Bucket="ad-images-tigris", Key=key)
        assert any(
            grant.get("Permission") == "READ"
            and grant.get("Grantee", {}).get("URI")
            == "http://acs.amazonaws.com/groups/global/AllUsers"
            for grant in acl["Grants"]
        )

        # The body is a valid 1080x1080 PNG
        obj = s3.get_object(Bucket="ad-images-tigris", Key=key)
        png_bytes = obj["Body"].read()
        img = Image.open(io.BytesIO(png_bytes))
        assert img.format == "PNG"
        assert img.size == (1080, 1080)

    asyncio.run(_run())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `tools.creative._s3_client` reads `AWS_ENDPOINT_URL` (KeyError) or returns a URL on `t3.storage.dev`.

- [ ] **Step 3: Rewrite the env-handling helpers in `tools/creative.py`**

Edit `tools/creative.py`. Replace the `_s3_client()` and `_upload_png()` functions (the block from `def _s3_client():` through the end of `def _upload_png():`) with:

```python
def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("TIGRIS_API_ENDPOINT", "https://t3.storage.dev"),
        region_name="auto",
        aws_access_key_id=os.environ["TIGRIS_STORAGE_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["TIGRIS_STORAGE_SECRET_ACCESS_KEY"],
        config=Config(s3={"addressing_style": "virtual"}),
    )


def _upload_png(png_bytes: bytes, key: str) -> str:
    bucket = os.environ["TIGRIS_STORAGE_BUCKET"]
    public_host = os.environ.get("TIGRIS_PUBLIC_HOST", "t3.tigrisfiles.io")
    s3 = _s3_client()
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=png_bytes,
        ACL="public-read",
        ContentType="image/png",
    )
    return f"https://{bucket}.{public_host}/{key}"
```

Also remove the now-unused `from urllib.parse import urlparse` import at the top of the file.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_render.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (8 schema + 1 scrape + 1 render = 10).

- [ ] **Step 6: Commit**

```bash
git add tools/creative.py tests/test_render.py
git commit -m "feat(creative): switch Tigris client to TIGRIS_STORAGE_* env + CDN host"
```

---

## Task 5: Add `view_brand_reference` tool

**Files:**
- Create: `tools/view.py`
- Create: `tests/test_view_reference.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_view_reference.py`:

```python
import asyncio
from unittest.mock import patch

import pytest


def _run(coro):
    return asyncio.run(coro)


def test_view_brand_reference_wraps_https_image_url_as_image_block():
    from tools.view import _view_handler

    result = _run(_view_handler({"url": "https://acme.com/img/hero.jpg"}))

    assert "content" in result
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block["type"] == "image"
    assert block["source"]["type"] == "url"
    assert block["source"]["url"] == "https://acme.com/img/hero.jpg"


@pytest.mark.parametrize(
    "ext", [".png", ".jpg", ".jpeg", ".webp", ".gif", ".PNG", ".JPG"]
)
def test_view_brand_reference_accepts_known_image_extensions_without_head(ext):
    from tools.view import _view_handler

    with patch("tools.view._head_content_type") as head_check:
        result = _run(_view_handler({"url": f"https://acme.com/img/photo{ext}"}))

    head_check.assert_not_called()
    assert result["content"][0]["type"] == "image"


def test_view_brand_reference_rejects_non_https():
    from tools.view import _view_handler

    result = _run(_view_handler({"url": "http://acme.com/img/hero.jpg"}))

    assert result["content"][0]["type"] == "text"
    assert "https" in result["content"][0]["text"].lower()


def test_view_brand_reference_rejects_file_scheme():
    from tools.view import _view_handler

    result = _run(_view_handler({"url": "file:///etc/passwd"}))

    assert result["content"][0]["type"] == "text"


def test_view_brand_reference_uses_head_for_extensionless_urls():
    from tools.view import _view_handler

    with patch("tools.view._head_content_type", return_value="image/png") as head_check:
        result = _run(_view_handler({"url": "https://cdn.example.com/asset/12345"}))

    head_check.assert_called_once_with("https://cdn.example.com/asset/12345")
    assert result["content"][0]["type"] == "image"


def test_view_brand_reference_rejects_non_image_content_type():
    from tools.view import _view_handler

    with patch("tools.view._head_content_type", return_value="text/html"):
        result = _run(_view_handler({"url": "https://cdn.example.com/asset/12345"}))

    assert result["content"][0]["type"] == "text"
    assert "image" in result["content"][0]["text"].lower()


def test_view_brand_reference_treats_head_failure_as_skip():
    from tools.view import _view_handler

    with patch("tools.view._head_content_type", return_value=None):
        result = _run(_view_handler({"url": "https://cdn.example.com/asset/12345"}))

    assert result["content"][0]["type"] == "text"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_view_reference.py -v`
Expected: collection error / `ModuleNotFoundError: No module named 'tools.view'`.

- [ ] **Step 3: Create `tools/view.py`**

```python
"""view_brand_reference tool — exposes a scraped image URL to the
creative-director subagent as an Anthropic image content block, so the model
can use vision to inform synthetic creative design without embedding the
asset in the final HTML."""
from typing import Any
from urllib.parse import urlparse

import urllib.error
import urllib.request

from claude_agent_sdk import tool

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_HEAD_TIMEOUT_SECONDS = 3.0


def _looks_like_image_url(path: str) -> bool:
    return path.lower().endswith(_IMAGE_EXTENSIONS)


def _head_content_type(url: str) -> str | None:
    """Return the Content-Type from a HEAD request, or None on failure."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=_HEAD_TIMEOUT_SECONDS) as resp:
            return resp.headers.get("Content-Type")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def _image(url: str) -> dict[str, Any]:
    return {
        "content": [
            {"type": "image", "source": {"type": "url", "url": url}}
        ]
    }


async def _view_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Validate the URL and return it as an image content block when safe.

    Validation rules:
      1. Scheme must be HTTPS.
      2. If the URL ends in a known image extension, accept it directly.
      3. Otherwise, perform one HEAD request with a 3-second timeout. Accept
         iff Content-Type starts with `image/`.

    On rejection, return a text content block describing the reason so the
    model can decide whether to skip this reference and continue.
    """
    url = args["url"]
    parsed = urlparse(url)

    if parsed.scheme != "https":
        return _text(
            f"Reference URL rejected (scheme={parsed.scheme!r}): "
            "only https:// URLs are allowed."
        )

    if _looks_like_image_url(parsed.path):
        return _image(url)

    content_type = _head_content_type(url)
    if content_type and content_type.lower().startswith("image/"):
        return _image(url)

    if content_type is None:
        return _text(
            f"Reference URL skipped: HEAD request to {url} failed or timed out."
        )

    return _text(
        f"Reference URL rejected (Content-Type={content_type!r}): "
        "expected an image/* type."
    )


view_brand_reference = tool(
    "view_brand_reference",
    "Fetch a scraped reference image URL and return it as an image content "
    "block so the creative director can see the brand's existing photography "
    "before composing synthetic HTML/CSS/SVG ad creatives.",
    {"url": str},
)(_view_handler)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_view_reference.py -v`
Expected: 9 passed (1 wraps URL + 7 parametrized extensions + 1 each for the other 5 cases = 13). Re-count: `test_view_brand_reference_wraps_https_image_url_as_image_block` (1) + parametrized test runs over 7 extensions (7) + `test_view_brand_reference_rejects_non_https` (1) + `test_view_brand_reference_rejects_file_scheme` (1) + `test_view_brand_reference_uses_head_for_extensionless_urls` (1) + `test_view_brand_reference_rejects_non_image_content_type` (1) + `test_view_brand_reference_treats_head_failure_as_skip` (1) = 13 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/view.py tests/test_view_reference.py
git commit -m "feat(view): add view_brand_reference tool for vision-input reference images"
```

---

## Task 6: Register `view_brand_reference` in the MCP server

**Files:**
- Modify: `tools/mcp_server.py`

- [ ] **Step 1: Edit `tools/mcp_server.py`**

Replace the entire contents of `tools/mcp_server.py` with:

```python
from claude_agent_sdk import create_sdk_mcp_server

from tools.creative import render_creative
from tools.scrape import scrape_url
from tools.view import view_brand_reference

SERVER_NAME = "adpipeline"

adpipeline_server = create_sdk_mcp_server(
    name=SERVER_NAME,
    version="0.2.0",
    tools=[scrape_url, render_creative, view_brand_reference],
)

# Fully-qualified tool names as exposed to the agent loop.
SCRAPE_URL_TOOL = f"mcp__{SERVER_NAME}__scrape_url"
RENDER_CREATIVE_TOOL = f"mcp__{SERVER_NAME}__render_creative"
VIEW_BRAND_REFERENCE_TOOL = f"mcp__{SERVER_NAME}__view_brand_reference"
```

- [ ] **Step 2: Verify the server still imports cleanly**

Run: `uv run python -c "from tools.mcp_server import adpipeline_server, SCRAPE_URL_TOOL, RENDER_CREATIVE_TOOL, VIEW_BRAND_REFERENCE_TOOL; print(VIEW_BRAND_REFERENCE_TOOL)"`
Expected output: `mcp__adpipeline__view_brand_reference`

- [ ] **Step 3: Commit**

```bash
git add tools/mcp_server.py
git commit -m "feat(mcp): register view_brand_reference + bump server version"
```

---

## Task 7: Add `agents.py` (coordinator prompt + 2 AgentDefinitions)

**Files:**
- Create: `agents.py`

- [ ] **Step 1: Create `agents.py`**

```python
"""Coordinator system prompt and AgentDefinitions for the meta-ad-pipeline.

Three agents in total:
- Coordinator: top-level query() with COORDINATOR_PROMPT. Owns scrape_url and
  decides variant_count, budget_override, status from user phrasing.
- creative-director: synthesizes HTML/CSS/SVG ad creatives. Tools:
  render_creative + view_brand_reference. No MCP servers.
- media-buyer: publishes ads via pipeboard MCP and answers analytics. Composes
  Meta campaign objective, optimization, budget, and targeting in context.
"""
from claude_agent_sdk import AgentDefinition

from tools.mcp_server import (
    RENDER_CREATIVE_TOOL,
    VIEW_BRAND_REFERENCE_TOOL,
)

COORDINATOR_PROMPT = """\
You are an ad-campaign assistant for Facebook/Meta advertising.

When the user asks you to build an ad for a URL:
  1. Decide variant_count from the user's phrasing:
     - "quick test", "just one", "single creative" -> 1
     - no qualifier, normal request -> 2 (default)
     - "test a bunch", "creative bake-off", "multiple angles" -> 3 or 4 (cap 4)
  2. Decide budget_override from the user's phrasing. Set it ONLY if the user
     gave an explicit number ($X/day, X dollars per day, etc.). Otherwise leave
     it unset and the media-buyer will cap at $50/day.
  3. Decide status:
     - default PAUSED
     - ACTIVE only if the user said "go live" or equivalent unambiguous phrase
  4. Call scrape_url with the URL and a concise extraction_goal to get a
     BrandResearch JSON (logo, colors, value prop, visual assets, tone, CTA text,
     a draft Problem/Solution copy idea).
  5. Delegate to the `creative-director` subagent. Pass the full BrandResearch
     JSON and the chosen variant_count. It returns a list of
     {variant_id, variant_note, png_url}.
  6. Delegate to the `media-buyer` subagent. Pass: landing URL, BrandResearch
     JSON, the list of png_urls, ad_account_id, page_id, status, and
     budget_override (if any).

When the user asks about existing campaigns or performance, delegate directly
to the `media-buyer` subagent.

You must never try to render images or call pipeboard tools yourself. Always
delegate those to the right subagent. Do not bake adset targeting, objective,
optimization goal, or budget into your delegation prompt - pass the source
material and let media-buyer decide.
"""


CREATIVE_DIRECTOR_PROMPT = """\
You are a creative director producing Facebook/Meta ad creatives.

You will receive:
  - A BrandResearch JSON with identity (logo_url, primary_color_hexes), value_prop,
    visual_asset_urls, tone_adjectives, cta_button_text, and creative_copy_idea
    (hook, body, headline).
  - A desired number of visual variants (default 2).

Workflow:
  1. If BrandResearch.visual_asset_urls is non-empty, call view_brand_reference once
     per URL (up to 3) to see what the brand's existing photography looks like.
     Use the imagery as inspiration for color, mood, and composition - do NOT embed
     these URLs in your HTML. Your output is fully synthetic.
  2. For each visual variant, compose ONE self-contained HTML document. Rules:
     - Full <!DOCTYPE html> with inline <style>.
     - Viewport MUST be 1080x1080: include `@page { size: 1080px 1080px; margin: 0 }`
       and set `html, body` to `width:1080px; height:1080px; margin:0; padding:0`.
     - No external <img> tags. Google Fonts via <link rel="stylesheet" ...> is allowed.
     - Inline <svg> for shapes/graphics, CSS gradients for backgrounds.
     - Palette MUST be derived from BrandResearch.identity.primary_color_hexes.
     - Visual hierarchy: dominant headline (use creative_copy_idea.headline verbatim),
       value_prop.top_3_benefits as supporting elements, CTA rendered as a prominent
       button with cta_button_text as the label.
     - Each variant must have a distinct visual direction (typographic, minimal, bold
       gradient, editorial, photo-realistic illustration, etc.). Declare that
       direction in `variant_note`.
  3. Call render_creative(html=..., variant_note=...) for each variant. It returns
     {variant_id, variant_note, png_url}.

After rendering, return a single final message with a JSON array of all
{variant_id, variant_note, png_url} entries. No prose. The parent coordinator
parses it.

You must not invent copy. Use creative_copy_idea fields as-is for headline / hook /
body. If a field doesn't fit a layout, prefer to redesign the layout, not the words.
"""


MEDIA_BUYER_PROMPT = """\
You are a Meta Ads media buyer. You have access to the pipeboard Meta Ads MCP
tools (campaign / adset / creative / ad create, insights, list, describe).

Two duties:

  (A) Publishing. When asked to publish ads, you receive:
        - landing_url, ad_account_id, page_id
        - BrandResearch JSON with all source material
        - A list of png_urls (Tigris public URLs for creative images)
        - status (PAUSED or ACTIVE)
        - optional budget_override (USD/day) if the user explicitly set one

      Compose the Meta ad fields from BrandResearch:
        - headline: usually creative_copy_idea.headline; shorten if >40 chars.
        - primary_text: combine creative_copy_idea.hook and creative_copy_idea.body
          with a blank line between. Trim to <=125 chars if needed; prefer keeping
          the full body and dropping/compressing the hook.
        - description: pick the strongest of value_prop.top_3_benefits and trim to
          <=30 chars.
        - call_to_action: map cta_button_text to the closest Meta enum (LEARN_MORE,
          SHOP_NOW, SIGN_UP, DOWNLOAD, GET_OFFER, BOOK_TRAVEL, CONTACT_US,
          SUBSCRIBE) using the brand's intent as context. Default LEARN_MORE when
          unclear.

      Compose the campaign + adset parameters from context (NOT from defaults):
        - objective: pick ONE of OUTCOME_TRAFFIC, OUTCOME_SALES, OUTCOME_LEADS,
          OUTCOME_AWARENESS, OUTCOME_ENGAGEMENT based on what the brand sells.
          Ecommerce/checkout pages -> OUTCOME_SALES. Lead-gen / B2B / form-based
          -> OUTCOME_LEADS. Content / blog / community -> OUTCOME_ENGAGEMENT.
          Pure traffic / "go visit our site" -> OUTCOME_TRAFFIC. Brand awareness
          with no conversion goal -> OUTCOME_AWARENESS.
        - optimization_goal + billing_event: pick the pair that matches the
          objective per Meta's matrix (e.g., OUTCOME_TRAFFIC -> LINK_CLICKS /
          IMPRESSIONS; OUTCOME_SALES -> OFFSITE_CONVERSIONS / IMPRESSIONS;
          OUTCOME_LEADS -> LEAD_GENERATION / IMPRESSIONS; OUTCOME_ENGAGEMENT ->
          POST_ENGAGEMENT / IMPRESSIONS; OUTCOME_AWARENESS -> REACH /
          IMPRESSIONS). When unsure, default to LINK_CLICKS / IMPRESSIONS.
        - daily_budget_cents: pick a sensible test budget in the range $5-$50/day
          (i.e., 500-5000 cents) based on the brand's apparent scale and the
          number of variants. SAFETY CEILING: never exceed $50/day unless
          budget_override is set; if the user did not explicitly authorize a
          higher number in their request, cap at 5000 cents and note in the
          return payload that the cap was applied.
        - targeting: infer from BrandResearch. Geo defaults to US unless the
          brand is clearly regional (e.g., a UK-only retailer -> GB). Age range:
          narrow from 18-65 toward the persona implied by tone_adjectives and
          value_prop (e.g., "playful, bold, viral" -> 18-34; "professional,
          enterprise, ROI" -> 28-55). Add 2-4 Meta interest targeting tags
          derived from value_prop.top_3_benefits.

      Then sequence: discover the upload-from-URL tool vs direct image_url path
      (one-time tool-list inspection), create_campaign with the chosen objective
      and requested status, create_adset with the chosen targeting/budget/
      optimization, create_ad_creative per png_url, create_ad per creative.
      Return JSON {campaign_id, adset_id, creative_ids, ad_ids, objective,
      daily_budget_cents, targeting_summary, budget_cap_applied, notes}.

  (B) Analytics. When asked about performance, call the appropriate pipeboard
      insights/list tools and summarize clearly. No more than 5 bullets.

Safety: default status is PAUSED. Only use ACTIVE when the user's request
explicitly contains "go live" or equivalent unambiguous activation language.
"""


def build_agents() -> dict[str, AgentDefinition]:
    return {
        "creative-director": AgentDefinition(
            description=(
                "Generates ad-creative HTML variants (inline CSS + SVG) and "
                "renders each to a 1080x1080 PNG on Tigris. Use whenever the "
                "user needs ad images for a campaign."
            ),
            prompt=CREATIVE_DIRECTOR_PROMPT,
            tools=[RENDER_CREATIVE_TOOL, VIEW_BRAND_REFERENCE_TOOL],
            model="inherit",
        ),
        "media-buyer": AgentDefinition(
            description=(
                "Publishes Meta/Facebook ads via the pipeboard MCP tools and "
                "answers performance / analytics questions about existing "
                "campaigns. Composes Meta campaign objective, optimization, "
                "budget, and targeting in context (no baked defaults)."
            ),
            prompt=MEDIA_BUYER_PROMPT,
            tools=None,  # inherit all - including the scoped pipeboard MCP tools
            mcpServers=["pipeboard"],
            model="inherit",
        ),
    }
```

- [ ] **Step 2: Verify it imports**

Run: `uv run python -c "from agents import build_agents, COORDINATOR_PROMPT; print(list(build_agents().keys()))"`
Expected output: `['creative-director', 'media-buyer']`

- [ ] **Step 3: Commit**

```bash
git add agents.py
git commit -m "feat(agents): add coordinator prompt + creative-director + media-buyer"
```

---

## Task 8: Add `tests/test_agents_smoke.py` (structural prompt assertions)

**Files:**
- Create: `tests/test_agents_smoke.py`

- [ ] **Step 1: Create the test file**

```python
"""Structural assertions on the agent prompts and tool wiring.

We can't replay the full claude-agent-sdk loop without an API key, so these
tests pin the prompt text. They catch regressions where someone re-bakes a
default that the v2 design says must stay agentic.
"""
from agents import (
    COORDINATOR_PROMPT,
    CREATIVE_DIRECTOR_PROMPT,
    MEDIA_BUYER_PROMPT,
    build_agents,
)
from tools.mcp_server import RENDER_CREATIVE_TOOL, VIEW_BRAND_REFERENCE_TOOL


def test_both_subagents_defined():
    agents = build_agents()
    assert set(agents.keys()) == {"creative-director", "media-buyer"}


def test_creative_director_tools_include_render_and_view():
    agents = build_agents()
    cd = agents["creative-director"]
    assert cd.tools == [RENDER_CREATIVE_TOOL, VIEW_BRAND_REFERENCE_TOOL]
    assert cd.mcpServers in (None, [])


def test_media_buyer_scoped_to_pipeboard():
    agents = build_agents()
    mb = agents["media-buyer"]
    assert mb.mcpServers == ["pipeboard"]
    assert mb.tools is None  # inherit, so pipeboard MCP tools are usable


def test_coordinator_prompt_keeps_decisions_agentic():
    p = COORDINATOR_PROMPT
    assert "scrape_url" in p
    assert "creative-director" in p
    assert "media-buyer" in p
    assert "variant_count" in p
    assert "budget_override" in p
    assert "PAUSED" in p
    assert "go live" in p
    # Negative directive: don't bake the things media-buyer must decide.
    assert "Do not bake adset targeting, objective, optimization goal, or budget" in p


def test_creative_director_prompt_uses_view_and_brand_inputs():
    p = CREATIVE_DIRECTOR_PROMPT
    assert "view_brand_reference" in p
    assert "primary_color_hexes" in p
    assert "creative_copy_idea.headline" in p
    assert "1080x1080" in p


def test_media_buyer_prompt_lists_all_objective_enums_and_budget_cap():
    p = MEDIA_BUYER_PROMPT
    for objective in (
        "OUTCOME_TRAFFIC",
        "OUTCOME_SALES",
        "OUTCOME_LEADS",
        "OUTCOME_AWARENESS",
        "OUTCOME_ENGAGEMENT",
    ):
        assert objective in p
    assert "5000 cents" in p or "$50/day" in p
    assert "budget_cap_applied" in p
    assert "targeting_summary" in p
    assert "PAUSED" in p
```

- [ ] **Step 2: Run the smoke test**

Run: `uv run pytest tests/test_agents_smoke.py -v`
Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_agents_smoke.py
git commit -m "test(agents): pin agent prompt structural invariants"
```

---

## Task 9: Add the Chainlit app

**Files:**
- Create: `app.py`
- Create: `chainlit.md`

- [ ] **Step 1: Create `chainlit.md` (welcome screen)**

```markdown
# Meta Ad Pipeline

Paste a landing URL and I'll:

1. Scrape the page for brand identity, value prop, tone, and reference photos.
2. Design 1-4 ad creatives (1080x1080 PNG, hosted on Tigris).
3. Publish them to Meta Ad Manager via pipeboard - PAUSED by default, $50/day budget cap.

After creation you can ask follow-ups like "how is the CTR on that campaign?"
and I'll query Meta live.

**Example prompts**

- `Build a Meta ad for https://acme.example.com, ad account act_123, page 456`
- `Quick test ad for https://acme.com, account act_123, page 456`
- `Creative bake-off, 4 variants, https://acme.com, act_123, page 456`
- `How is campaign abc123 doing so far?`
- `Go live on campaign abc123 at $200/day`
```

- [ ] **Step 2: Create `app.py`**

```python
"""Chainlit entrypoint for the meta-ad-pipeline.

Single-process app. Each user turn calls claude_agent_sdk.query(...) with the
coordinator prompt and the two AgentDefinitions. SDK session id is captured
from the first ResultMessage and stored in cl.user_session so subsequent turns
resume the same agent context. Subagent activity is shown as nested steps;
Tigris PNG URLs that appear in streamed text get rendered inline as images.
"""
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
_TIGRIS_PUBLIC_HOST = os.environ.get("TIGRIS_PUBLIC_HOST", "t3.tigrisfiles.io")
_TIGRIS_URL_PATTERN = re.compile(
    rf"https://[a-zA-Z0-9_\-\.]+\.{re.escape(_TIGRIS_PUBLIC_HOST)}/creatives/[a-f0-9]+\.png"
)


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


def _extract_tigris_urls(text: str) -> list[str]:
    """Find Tigris public PNG URLs in streamed text so the UI can preview them."""
    return list(dict.fromkeys(_TIGRIS_URL_PATTERN.findall(text)))


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
        if isinstance(event, ResultMessage):
            if event.session_id:
                cl.user_session.set(_SDK_SESSION_KEY, event.session_id)
            continue

        if isinstance(event, SystemMessage):
            continue

        if not isinstance(event, AssistantMessage):
            continue

        parent_id = getattr(event, "parent_tool_use_id", None)
        if parent_id and parent_id not in seen_tool_use_ids:
            seen_tool_use_ids.add(parent_id)
            current_subagent_step = cl.Step(name="subagent", type="run")
            await current_subagent_step.__aenter__()

        for block in event.content:
            if isinstance(block, TextBlock):
                if parent_id and current_subagent_step is not None:
                    current_subagent_step.output = (
                        (current_subagent_step.output or "") + block.text
                    )
                    await current_subagent_step.update()
                else:
                    if assistant_msg is None:
                        assistant_msg = await cl.Message(content="").send()
                    await assistant_msg.stream_token(block.text)

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
```

- [ ] **Step 3: Verify the app imports cleanly**

Run: `PIPEBOARD_OAUTH_TOKEN=dummy uv run python -c "import app; print('app imports:', app.MODEL)"`
Expected output: `app imports: claude-opus-4-7`

- [ ] **Step 4: Boot the Chainlit dev server (smoke)**

Run (foreground; Ctrl+C after the welcome screen renders):

```bash
uv run chainlit run app.py -w
```

Open `http://localhost:8000` in a browser. Expected: the welcome content from `chainlit.md` renders. No import / runtime errors in the terminal. End the smoke with Ctrl+C.

- [ ] **Step 5: Commit**

```bash
git add app.py chainlit.md
git commit -m "feat(app): Chainlit UI with session resume and Tigris CDN inline previews"
```

---

## Task 10: Add the v2 manual integration checklist

**Files:**
- Create: `docs/superpowers/integration-checklist-v2.md`

- [ ] **Step 1: Write the checklist**

```markdown
# Manual Integration Checklist (v2)

Run before merging or releasing. Not automated because each step touches
external services with real costs or OAuth tokens.

## Prerequisites

- [ ] `.env` populated with: `BROWSER_USE_API_KEY`, `PIPEBOARD_OAUTH_TOKEN`,
      `TIGRIS_STORAGE_ACCESS_KEY_ID`, `TIGRIS_STORAGE_SECRET_ACCESS_KEY`,
      `TIGRIS_STORAGE_BUCKET=ad-images-tigris`. Either `ANTHROPIC_API_KEY` set
      OR Claude Code CLI logged in.
- [ ] Tigris bucket `ad-images-tigris` exists and is publicly readable.
- [ ] Pipeboard OAuth token is fresh (obtain via
      `npx @modelcontextprotocol/inspector` against
      `https://meta-ads.mcp.pipeboard.co/`).
- [ ] Sandbox Meta ad account + page ID available.

## Boot smoke

- [ ] `uv run chainlit run app.py -w` starts without import errors.
- [ ] `http://localhost:8000` renders the welcome screen from `chainlit.md`.

## Tigris CDN smoke

- [ ] Manually upload a 1080x1080 PNG to the bucket (e.g., via the Tigris
      console). Confirm it is fetchable at
      `https://ad-images-tigris.t3.tigrisfiles.io/<key>` from a fresh browser
      tab with no auth cookies.

## Scrape path

- [ ] Send: `Scrape https://example.com for ad copy`.
- [ ] Observe: coordinator calls `scrape_url` (visible in UI), Browser Use
      Cloud runs, returns a `BrandResearch` JSON, coordinator summarizes the
      identity, value prop, tone, and creative_copy_idea fields in chat.

## Reference image vision smoke

- [ ] After a scrape on a page with `visual_asset_urls`, observe the
      creative-director step calling `view_brand_reference` once per URL. If
      any reference URL fails the HEAD check, the subagent skips it gracefully
      and continues.

## Creative path

- [ ] Send: `Build a Meta ad for https://example.com, 2 variants, ad account
      act_SANDBOX, page 12345`.
- [ ] Observe:
  - `creative-director` subagent appears as a nested step.
  - Two `render_creative` tool calls complete.
  - Two inline image previews display in the chat (URLs on
    `*.t3.tigrisfiles.io`).
  - PNGs open in a browser and are 1080x1080.

## Variant count from phrasing

- [ ] Send: `Quick test ad for https://example.com, act_SANDBOX, page 12345`.
- [ ] Observe: creative-director produces exactly 1 variant.
- [ ] Send: `Creative bake-off, 4 variants, https://example.com, act_SANDBOX,
      page 12345`.
- [ ] Observe: creative-director produces 4 variants.

## Publish path with agentic decisions

- [ ] After a build request, the `media-buyer` step:
  - Inspects pipeboard's tool list once and picks upload-from-URL vs direct
    `image_url`.
  - Picks an objective (OUTCOME_TRAFFIC / SALES / LEADS / AWARENESS /
    ENGAGEMENT) based on the brand. Verify in the returned summary.
  - Picks an optimization_goal + billing_event pair matching that objective.
  - Picks a targeting set (geo, age range, 2-4 interests) inferred from the
    brand. Verify in `targeting_summary`.
  - Picks a daily_budget_cents in 500-5000. Verify it's <= 5000.
  - Includes `budget_cap_applied: true` in the response.
- [ ] In Meta Ad Manager: campaign exists, status PAUSED, daily budget <=
      $50.

## Budget override

- [ ] Send: `Build an ad for https://example.com, act_SANDBOX, page 12345,
      go live at $200/day`.
- [ ] Observe: media-buyer's response shows `daily_budget_cents: 20000` and
      `budget_cap_applied: false`. In Meta Ad Manager: status ACTIVE, daily
      budget $200.

## Status flip safety

- [ ] Send: `Go live on campaign abc123` (replace abc123).
- [ ] Observe: status flips to ACTIVE but daily budget is unchanged from
      whatever it was at PAUSED.

## Analytics path

- [ ] Send: `How is campaign <id> doing?`.
- [ ] Observe: coordinator delegates to `media-buyer`, which calls insights
      tools and returns a 5-bullet-or-fewer summary.

## Failure paths

- [ ] Invalid URL: coordinator surfaces the Browser Use Cloud error; no ad is
      created.
- [ ] Reference URL 404 / hotlinking 403: `view_brand_reference` returns a
      text content block describing the skip; creative-director continues
      with the remaining references (or none).
- [ ] Malformed HTML from creative-director: `render_creative` surfaces the
      weasyprint error; subagent retries with a revised variant.
- [ ] Expired pipeboard token: `media-buyer` surfaces 401; coordinator tells
      the user to refresh via the MCP inspector.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/integration-checklist-v2.md
git commit -m "docs: add v2 manual integration checklist"
```

---

## Task 11: Final verification

**Files:** none modified.

- [ ] **Step 1: Run the full automated suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass. Approximate count:
- `test_schemas.py`: 8
- `test_scrape.py`: 1
- `test_render.py`: 1
- `test_view_reference.py`: 13
- `test_agents_smoke.py`: 6
- Total: 29 passed.

- [ ] **Step 2: Verify the in-process MCP server registers all 3 tools**

Run: `uv run python -c "from tools.mcp_server import adpipeline_server, SCRAPE_URL_TOOL, RENDER_CREATIVE_TOOL, VIEW_BRAND_REFERENCE_TOOL; print(SCRAPE_URL_TOOL); print(RENDER_CREATIVE_TOOL); print(VIEW_BRAND_REFERENCE_TOOL)"`
Expected output:
```
mcp__adpipeline__scrape_url
mcp__adpipeline__render_creative
mcp__adpipeline__view_brand_reference
```

- [ ] **Step 3: Verify the app imports with both agents wired**

Run: `PIPEBOARD_OAUTH_TOKEN=dummy uv run python -c "import app; print(list(app.build_agents().keys()))"`
Expected output: `['creative-director', 'media-buyer']`

- [ ] **Step 4: Walk through the v2 manual integration checklist**

Open `docs/superpowers/integration-checklist-v2.md` and complete it end-to-end against the sandbox Meta ad account. Capture any deviations as new issues to address.

---

## Post-implementation

- [ ] All automated tests pass: `uv run pytest tests/ -v`.
- [ ] `.env` is created locally and gitignored (verify with `git status`).
- [ ] Manual integration checklist (Task 10 file) completed at least once on a
      sandbox Meta ad account.
- [ ] Confirm Tigris bucket retention policy is acceptable (keep-forever is OK
      for v1; revisit if cost pressure arises).

## Known gaps to revisit (carried from spec §11)

1. **Pipeboard tool discovery.** The `media-buyer` prompt assumes tool names
   that match Meta's API. On first run, inspect the live tool list (via
   `npx @modelcontextprotocol/inspector` against
   `https://meta-ads.mcp.pipeboard.co/`) and reconcile. Update the prompt if
   names differ.
2. **URL acceptance on pipeboard.** `media-buyer` is told to inspect the tool
   list once and pick upload-from-URL vs direct image_url. Validate which path
   works and pin the prompt to that path if needed.
3. **Variant rendering concurrency.** Currently sequential. If perceived slow,
   wrap creative-director's render loop in `asyncio.gather`. Diminishing
   returns past ~3 in-flight (weasyprint is CPU-bound).
4. **`view_brand_reference` HEAD timeout.** 3 seconds. If unresponsive sites
   slow the subagent too much, drop to 1.5s.
5. **Per-call image size cap on `view_brand_reference`.** Currently
   unenforced. Add if a giant image inflates context.
6. **$50/day budget ceiling.** Currently a prompt rule. Promote to
   `META_DAILY_BUDGET_CEILING_CENTS` env var if real usage tunes it.
7. **Pipeboard's `optimization_goal` / `billing_event` enum names.** May
   differ from raw Meta API names. Adjust the matrix in `MEDIA_BUYER_PROMPT`
   if needed.
