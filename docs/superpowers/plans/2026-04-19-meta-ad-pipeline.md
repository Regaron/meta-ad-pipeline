# Meta Ad Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a conversational web app where Claude delegates URL scraping (Browser Use Cloud), ad-creative generation (weasyprint + Tigris), and Meta ad publishing (pipeboard MCP) to specialized subagents orchestrated via `claude-agent-sdk`.

**Architecture:** One Chainlit Python process. A top-level coordinator `query()` calls `scrape_url` directly and delegates design + publishing to two `AgentDefinition` subagents (`creative-director`, `media-buyer`). Images rasterize via `weasyprint → pypdfium2 → PIL`, upload to Tigris, and only their URLs cross into pipeboard MCP. Chainlit's data layer persists chat history; no custom DB schema.

**Tech Stack:** Python 3.11+, `claude-agent-sdk`, `browser-use-sdk`, `weasyprint`, `pypdfium2`, `pillow`, `boto3`, `chainlit`, `pydantic`, `uv` for package management.

**Spec:** `docs/superpowers/specs/2026-04-19-meta-ad-pipeline-design.md`.

---

## Task 1: Clean up obsolete files and add .gitignore

**Files:**
- Delete: `coordinator.py`, `browser_agent.py`, `image_agent.py`, `ads_agent.py`, `signatures.py`, `pyproject.toml`, `.env.example`, `__pycache__/`, `.DS_Store`
- Create: `.gitignore`

- [ ] **Step 1: Delete obsolete files from the old design**

```bash
rm -f coordinator.py browser_agent.py image_agent.py ads_agent.py signatures.py pyproject.toml .env.example .DS_Store
rm -rf __pycache__
```

- [ ] **Step 2: Create .gitignore**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Env
.env
.env.local

# OS
.DS_Store

# Chainlit
.chainlit/
.files/
chainlit.yaml

# Logs
*.log
```

- [ ] **Step 3: Verify working tree is clean of old artifacts**

Run: `ls -A`
Expected: `.git  .gitignore  .remember  docs` (only these four entries)

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: remove old design files and add gitignore"
```

---

## Task 2: Create pyproject.toml and .env.example

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "ad-pipeline"
version = "0.1.0"
description = "Conversational Meta ad pipeline: URL -> copy -> HTML creatives -> Tigris-hosted PNGs -> published Meta ads"
requires-python = ">=3.11"
dependencies = [
    "claude-agent-sdk>=0.2.111",
    "browser-use-sdk>=3.0",
    "weasyprint>=62",
    "pypdfium2>=4",
    "pillow>=11",
    "boto3>=1.35",
    "chainlit>=2.0",
    "pydantic>=2.9",
    "python-dotenv>=1.0",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "moto>=5",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["tools"]
```

- [ ] **Step 2: Create .env.example**

```bash
# Anthropic auth — ONE of:
#   (a) logged in via `claude` CLI (OAuth to Pro/Max subscription, local dev only)
#   (b) ANTHROPIC_API_KEY (required for non-local or multi-user deployment)
# ANTHROPIC_API_KEY=

# Browser Use Cloud — get at https://cloud.browser-use.com
BROWSER_USE_API_KEY=bu_...

# Pipeboard Meta Ads MCP
# Obtain via: npx @modelcontextprotocol/inspector
#   -> Transport: Streamable HTTP
#   -> URL: https://meta-ads.mcp.pipeboard.co/
#   -> Open Auth Settings -> Quick OAuth Flow -> copy access_token
PIPEBOARD_OAUTH_TOKEN=

# Tigris (S3-compatible object storage)
AWS_ACCESS_KEY_ID=tid_...
AWS_SECRET_ACCESS_KEY=tsec_...
AWS_ENDPOINT_URL=https://t3.storage.dev
AWS_REGION=auto
TIGRIS_BUCKET=ad-pipeline-creatives
```

- [ ] **Step 3: Install dependencies with uv**

```bash
uv sync
```

Expected: creates `.venv/` and `uv.lock`; all dependencies install cleanly. Note: `weasyprint` may require system libs (`libpango`, `libcairo`) — on macOS install with `brew install pango`.

- [ ] **Step 4: Verify imports**

```bash
uv run python -c "import claude_agent_sdk, browser_use_sdk, weasyprint, pypdfium2, PIL, boto3, chainlit, pydantic; print('ok')"
```

Expected output: `ok`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .env.example uv.lock
git commit -m "chore: add pyproject.toml with pinned deps and env template"
```

---

## Task 3: Define Pydantic schemas

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/schemas.py`
- Create: `tests/__init__.py`
- Create: `tests/test_schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schemas.py
from tools.schemas import AdCopy, RenderedCreative


def test_ad_copy_validates():
    ac = AdCopy(
        headline="Save on Widgets",
        primary_text="Get 20% off premium widgets today.",
        description="Shop now.",
        value_props=["Free shipping", "30-day returns", "Lifetime warranty"],
        call_to_action="SHOP_NOW",
        brand_color_theme="warm sunset oranges",
    )
    assert ac.headline == "Save on Widgets"
    assert ac.call_to_action == "SHOP_NOW"
    assert len(ac.value_props) == 3


def test_ad_copy_rejects_invalid_cta():
    import pydantic
    try:
        AdCopy(
            headline="x",
            primary_text="x",
            description="x",
            value_props=["a"],
            call_to_action="NOT_A_REAL_CTA",
            brand_color_theme="x",
        )
    except pydantic.ValidationError:
        return
    raise AssertionError("expected ValidationError for invalid CTA")


def test_rendered_creative_roundtrip():
    rc = RenderedCreative(
        variant_id="abc123",
        variant_note="bold typographic",
        png_url="https://bucket.t3.storage.dev/creatives/abc123.png",
    )
    assert rc.variant_id == "abc123"
    assert rc.png_url.startswith("https://")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools'`

- [ ] **Step 3: Create tools/__init__.py and tools/schemas.py**

`tools/__init__.py`: (empty file)

```python
# tools/schemas.py
from typing import Literal
from pydantic import BaseModel, Field

CallToAction = Literal[
    "LEARN_MORE",
    "SHOP_NOW",
    "SIGN_UP",
    "DOWNLOAD",
    "GET_OFFER",
    "BOOK_TRAVEL",
    "CONTACT_US",
    "SUBSCRIBE",
]


class AdCopy(BaseModel):
    """Structured ad copy extracted from a landing page."""

    headline: str = Field(..., description="Short headline, target <= 40 chars.")
    primary_text: str = Field(..., description="Ad body text, target <= 125 chars.")
    description: str = Field(..., description="Link description, target <= 30 chars.")
    value_props: list[str] = Field(..., description="3-5 short selling-point bullets.")
    call_to_action: CallToAction = Field(..., description="Facebook CTA button type.")
    brand_color_theme: str = Field(
        ...,
        description="Color theme phrase, e.g. 'warm sunset oranges', 'clean tech blue'.",
    )


class RenderedCreative(BaseModel):
    """A rendered ad creative uploaded to Tigris."""

    variant_id: str
    variant_note: str
    png_url: str
```

- [ ] **Step 4: Create empty tests/__init__.py**

```python
# tests/__init__.py
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add tools/__init__.py tools/schemas.py tests/__init__.py tests/test_schemas.py
git commit -m "feat(tools): add AdCopy and RenderedCreative Pydantic schemas"
```

---

## Task 4: scrape_url tool (Browser Use Cloud)

**Files:**
- Create: `tools/scrape.py`
- Create: `tests/test_scrape.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scrape.py
import json
from unittest.mock import AsyncMock, patch

import pytest

from tools.schemas import AdCopy


_CANNED_COPY = AdCopy(
    headline="Premium Widgets — 20% Off",
    primary_text="High-grade widgets built to last.",
    description="Shop widgets.",
    value_props=["Free shipping", "Lifetime warranty", "30-day returns"],
    call_to_action="SHOP_NOW",
    brand_color_theme="warm sunset oranges",
)


@pytest.mark.asyncio
async def test_scrape_url_returns_text_block_with_adcopy_json():
    """scrape_url wraps browser-use-sdk and returns an AdCopy serialized to JSON text."""
    from tools.scrape import scrape_url

    fake_result = AsyncMock()
    fake_result.output = _CANNED_COPY

    fake_client = AsyncMock()
    fake_client.run = AsyncMock(return_value=fake_result)

    with patch("tools.scrape.AsyncBrowserUse", return_value=fake_client):
        # Call the underlying coroutine directly to sidestep SdkMcpTool internals.
        from tools.scrape import _scrape_handler

        result = await _scrape_handler(
            {"url": "https://acme.com", "extraction_goal": "Extract ad copy."}
        )

    assert "content" in result
    assert len(result["content"]) == 1
    block = result["content"][0]
    assert block["type"] == "text"

    parsed = AdCopy.model_validate_json(block["text"])
    assert parsed.headline == "Premium Widgets — 20% Off"
    assert parsed.call_to_action == "SHOP_NOW"

    fake_client.run.assert_awaited_once()
    call_kwargs = fake_client.run.await_args.kwargs
    assert "https://acme.com" in call_kwargs["task"]
    assert call_kwargs["output_schema"] is AdCopy
    assert call_kwargs["model"] == "claude-opus-4.6"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_scrape.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.scrape'`

- [ ] **Step 3: Create tools/scrape.py**

```python
# tools/scrape.py
from typing import Any

from browser_use_sdk.v3 import AsyncBrowserUse
from claude_agent_sdk import tool

from tools.schemas import AdCopy


async def _scrape_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Drive Browser Use Cloud to extract AdCopy from a landing page.

    Exposed as a plain coroutine so tests can call it without depending on
    SdkMcpTool internals. The @tool-decorated `scrape_url` below wraps it
    for registration with create_sdk_mcp_server.
    """
    client = AsyncBrowserUse()
    task = (
        f"Visit {args['url']} and extract ad-copy material for a Facebook/Meta ad. "
        f"Additional focus: {args['extraction_goal']}. "
        "Extract a catchy headline (<=40 chars), primary body text (<=125 chars), "
        "short link description (<=30 chars), 3-5 value-prop bullets, a Facebook CTA "
        "(LEARN_MORE / SHOP_NOW / SIGN_UP / DOWNLOAD / GET_OFFER / BOOK_TRAVEL / "
        "CONTACT_US / SUBSCRIBE), and a short brand-color-theme phrase."
    )
    result = await client.run(
        task=task,
        model="claude-opus-4.6",
        output_schema=AdCopy,
    )
    ad_copy: AdCopy = result.output
    return {
        "content": [
            {"type": "text", "text": ad_copy.model_dump_json()}
        ]
    }


scrape_url = tool(
    "scrape_url",
    "Scrape a landing URL via Browser Use Cloud and return structured ad copy.",
    {"url": str, "extraction_goal": str},
)(_scrape_handler)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_scrape.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add tools/scrape.py tests/test_scrape.py
git commit -m "feat(tools): add scrape_url tool backed by Browser Use Cloud"
```

---

## Task 5: render_creative tool (weasyprint + pypdfium2 + Tigris)

**Files:**
- Create: `tools/creative.py`
- Create: `tests/test_render.py`
- Create: `tests/fixtures/sample_creative.html`

- [ ] **Step 1: Create the fixture HTML**

```html
<!-- tests/fixtures/sample_creative.html -->
<!DOCTYPE html>
<html>
<head>
  <style>
    @page { size: 1080px 1080px; margin: 0; }
    html, body { margin: 0; padding: 0; width: 1080px; height: 1080px; }
    body {
      background: linear-gradient(135deg, #ff7a18 0%, #ffcc33 100%);
      font-family: sans-serif;
      color: #111;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    h1 { font-size: 80px; margin: 0; text-align: center; }
  </style>
</head>
<body>
  <h1>Test Creative</h1>
</body>
</html>
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_render.py
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws
from PIL import Image

FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "sample_creative.html").read_text()


@pytest.fixture(autouse=True)
def _tigris_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://t3.storage.dev")
    monkeypatch.setenv("AWS_REGION", "auto")
    monkeypatch.setenv("TIGRIS_BUCKET", "ad-pipeline-creatives")


@pytest.mark.asyncio
@mock_aws
async def test_render_creative_produces_1080_png_and_uploads_to_tigris():
    """render_creative must: render PDF -> PNG, upload with public-read ACL, return URL."""
    # Arrange: create the bucket in moto
    s3 = boto3.client(
        "s3",
        endpoint_url="https://t3.storage.dev",
        region_name="us-east-1",  # moto ignores "auto"
    )
    s3.create_bucket(Bucket="ad-pipeline-creatives")

    from tools.creative import _render_handler

    # moto intercepts boto3 regardless of endpoint_url
    result = await _render_handler(
        {"html": FIXTURE_HTML, "variant_note": "bold gradient test"}
    )

    assert "content" in result
    payload = json.loads(result["content"][0]["text"])
    assert payload["variant_note"] == "bold gradient test"
    assert payload["png_url"].startswith(
        "https://ad-pipeline-creatives.t3.storage.dev/creatives/"
    )
    assert payload["png_url"].endswith(".png")

    # The object exists in the mock bucket with correct Content-Type
    key = payload["png_url"].split(".t3.storage.dev/")[1]
    head = s3.head_object(Bucket="ad-pipeline-creatives", Key=key)
    assert head["ContentType"] == "image/png"

    # The body is a valid 1080x1080 PNG
    obj = s3.get_object(Bucket="ad-pipeline-creatives", Key=key)
    png_bytes = obj["Body"].read()
    img = Image.open(io.BytesIO(png_bytes))
    assert img.format == "PNG"
    assert img.size == (1080, 1080)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.creative'`

- [ ] **Step 4: Create tools/creative.py**

```python
# tools/creative.py
import io
import json
import os
import uuid
from typing import Any

import boto3
import pypdfium2 as pdfium
import weasyprint
from botocore.client import Config
from claude_agent_sdk import tool
from PIL import Image

_PNG_SIZE = (1080, 1080)


def _render_html_to_png_bytes(html: str) -> bytes:
    """HTML -> PDF -> PNG bytes at 1080x1080."""
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    page = pdf[0]
    # WeasyPrint maps CSS px at 96 DPI; PDF's native DPI is 72.
    # Scale = 96/72 lifts the render back up to one pixel per CSS px.
    pil_img = page.render(scale=96 / 72).to_pil()
    if pil_img.size != _PNG_SIZE:
        pil_img = pil_img.resize(_PNG_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_REGION", "auto"),
        config=Config(s3={"addressing_style": "virtual"}),
    )


def _upload_png(png_bytes: bytes, key: str) -> str:
    bucket = os.environ["TIGRIS_BUCKET"]
    _s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=png_bytes,
        ACL="public-read",
        ContentType="image/png",
    )
    return f"https://{bucket}.t3.storage.dev/{key}"


async def _render_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Render HTML -> 1080x1080 PNG -> Tigris. Exposed as plain coroutine
    for testability; the @tool-decorated `render_creative` below wraps it."""
    variant_id = uuid.uuid4().hex[:10]
    png_bytes = _render_html_to_png_bytes(args["html"])
    key = f"creatives/{variant_id}.png"
    url = _upload_png(png_bytes, key)
    payload = {
        "variant_id": variant_id,
        "variant_note": args["variant_note"],
        "png_url": url,
    }
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


render_creative = tool(
    "render_creative",
    "Render an HTML ad creative to a 1080x1080 PNG on Tigris. Returns the public URL.",
    {"html": str, "variant_note": str},
)(_render_handler)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_render.py -v`
Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add tools/creative.py tests/test_render.py tests/fixtures/sample_creative.html
git commit -m "feat(tools): add render_creative tool (weasyprint -> pypdfium2 -> Tigris)"
```

---

## Task 6: Register tools in an in-process MCP server

**Files:**
- Create: `tools/mcp_server.py`

- [ ] **Step 1: Create tools/mcp_server.py**

```python
# tools/mcp_server.py
from claude_agent_sdk import create_sdk_mcp_server

from tools.creative import render_creative
from tools.scrape import scrape_url

SERVER_NAME = "adpipeline"

adpipeline_server = create_sdk_mcp_server(
    name=SERVER_NAME,
    version="0.1.0",
    tools=[scrape_url, render_creative],
)

# Fully-qualified tool names as exposed to the agent loop.
SCRAPE_URL_TOOL = f"mcp__{SERVER_NAME}__scrape_url"
RENDER_CREATIVE_TOOL = f"mcp__{SERVER_NAME}__render_creative"
```

- [ ] **Step 2: Verify the server builds without error**

Run: `uv run python -c "from tools.mcp_server import adpipeline_server, SCRAPE_URL_TOOL, RENDER_CREATIVE_TOOL; print(SCRAPE_URL_TOOL, RENDER_CREATIVE_TOOL)"`
Expected output: `mcp__adpipeline__scrape_url mcp__adpipeline__render_creative`

- [ ] **Step 3: Commit**

```bash
git add tools/mcp_server.py
git commit -m "feat(tools): register in-process MCP server with scrape+render tools"
```

---

## Task 7: Define agents (coordinator prompt + 2 subagents)

**Files:**
- Create: `agents.py`

- [ ] **Step 1: Create agents.py**

```python
# agents.py
"""Coordinator prompt + creative-director and media-buyer AgentDefinitions."""
from claude_agent_sdk import AgentDefinition

COORDINATOR_PROMPT = """\
You are an ad-campaign assistant for Facebook/Meta advertising.

When the user asks you to build an ad for a URL:
  1. Call scrape_url with the URL and a concise extraction_goal to get AdCopy.
  2. Delegate to the `creative-director` subagent with the AdCopy and the desired
     number of variants. It returns a list of {variant_id, variant_note, png_url}.
  3. Delegate to the `media-buyer` subagent with the landing URL, the AdCopy,
     the list of png_urls, the target ad_account_id, and the target page_id.
     Default ad status to PAUSED unless the user explicitly said "go live".

When the user asks about existing campaigns or performance, delegate directly
to the `media-buyer` subagent (it owns both the publish tools and the insights
tools).

You must never try to render images yourself or call pipeboard tools yourself.
Always delegate those to the right subagent.
"""


CREATIVE_DIRECTOR_PROMPT = """\
You are a creative director producing Facebook/Meta ad creatives.

You will receive:
  - AdCopy (headline, primary_text, description, value_props, call_to_action,
    brand_color_theme).
  - A desired number of variants (default 2).
  - A human-readable CTA label for the button (derive from call_to_action).

For each variant:
  1. Compose ONE self-contained HTML document. Rules:
     - Full <!DOCTYPE html> document with inline <style>.
     - Viewport MUST be 1080x1080: include `@page { size: 1080px 1080px; margin: 0 }`
       and set `html, body` to `width:1080px; height:1080px; margin:0; padding:0`.
     - No external <img> tags. Google Fonts via <link rel="stylesheet" ...> is allowed.
     - Use inline <svg> for shapes/graphics, CSS gradients for backgrounds.
     - Strong visual hierarchy: dominant headline, value props as styled elements,
       CTA rendered as a prominent button-like element with the CTA label.
     - Palette aligned with `brand_color_theme`.
     - Each variant must have a distinct visual direction (typographic, minimal,
       bold gradient, editorial, etc.). Declare that direction as `variant_note`.
  2. Call `render_creative(html=<the HTML string>, variant_note=<direction phrase>)`.
     It returns `{variant_id, variant_note, png_url}`.

After rendering all variants, return a single final message containing a JSON
array of all {variant_id, variant_note, png_url} entries. No prose. The parent
coordinator will parse it.
"""


MEDIA_BUYER_PROMPT = """\
You are a Meta Ads media buyer. You have access to the pipeboard Meta Ads MCP
tools (campaign/adset/creative/ad create, insights, list, describe).

Two duties:

  (A) Publishing. When asked to publish ads, you receive:
        - landing_url, ad_account_id, page_id
        - AdCopy fields (headline, primary_text, description, call_to_action)
        - A list of png_urls (Tigris public URLs for creative images)
        - status (PAUSED or ACTIVE)
      Sequence:
        1. For each png_url, either (a) call an upload-from-URL pipeboard tool
           if one exists to obtain a Meta image hash, or (b) pass the URL directly
           as `image_url` to `mcp_meta_ads_create_ad_creative`. Discover which is
           available by inspecting the server's tool list once and picking.
        2. Call `mcp_meta_ads_create_campaign` with objective OUTCOME_TRAFFIC and
           the requested status.
        3. Call `mcp_meta_ads_create_adset` with broad US targeting (age 18-65)
           and a $10/day daily_budget (1000 cents), optimization_goal LINK_CLICKS,
           billing_event IMPRESSIONS.
        4. For each image, call `mcp_meta_ads_create_ad_creative`.
        5. For each creative, call `mcp_meta_ads_create_ad`.
        6. Return a final JSON object: {campaign_id, adset_id, creative_ids,
           ad_ids, notes}. No prose.

  (B) Analytics. When asked about performance, call the appropriate pipeboard
      insights/list tools and summarize clearly. No more than 5 bullets.

Safety: default status is PAUSED. Only use ACTIVE when the user's request
explicitly contains "go live" or equivalent unambiguous activation language.
"""


def build_agents() -> dict[str, AgentDefinition]:
    from tools.mcp_server import RENDER_CREATIVE_TOOL

    return {
        "creative-director": AgentDefinition(
            description=(
                "Generates ad-creative HTML variants (inline CSS + SVG) and renders "
                "each to a 1080x1080 PNG on Tigris. Use whenever the user needs ad "
                "images for a campaign."
            ),
            prompt=CREATIVE_DIRECTOR_PROMPT,
            tools=[RENDER_CREATIVE_TOOL],
            model="inherit",
        ),
        "media-buyer": AgentDefinition(
            description=(
                "Publishes Meta/Facebook ads via the pipeboard MCP tools and "
                "answers performance / analytics questions about existing campaigns."
            ),
            prompt=MEDIA_BUYER_PROMPT,
            tools=None,  # inherit all — including the scoped pipeboard MCP tools
            mcpServers=["pipeboard"],
            model="inherit",
        ),
    }
```

- [ ] **Step 2: Verify agents.py imports cleanly**

Run: `uv run python -c "from agents import build_agents, COORDINATOR_PROMPT; print(list(build_agents().keys()))"`
Expected output: `['creative-director', 'media-buyer']`

- [ ] **Step 3: Commit**

```bash
git add agents.py
git commit -m "feat: add coordinator prompt and 2 AgentDefinitions"
```

---

## Task 8: Coordinator smoke test (monkey-patched query)

**Files:**
- Create: `tests/test_agents_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agents_smoke.py
"""Offline smoke: agents.build_agents and COORDINATOR_PROMPT are consistent.

We can't easily replay the full claude-agent-sdk loop without a real API key,
so we validate structural invariants that matter for correctness:
  - Both subagents exist with the expected keys.
  - creative-director has render_creative in its tools list.
  - media-buyer has pipeboard in its mcpServers list.
  - Coordinator prompt references both subagents by name and mentions scrape_url.
"""
import pytest

from agents import COORDINATOR_PROMPT, build_agents
from tools.mcp_server import RENDER_CREATIVE_TOOL


def test_both_subagents_defined():
    agents = build_agents()
    assert set(agents.keys()) == {"creative-director", "media-buyer"}


def test_creative_director_has_only_render_tool():
    agents = build_agents()
    cd = agents["creative-director"]
    assert cd.tools == [RENDER_CREATIVE_TOOL]
    assert cd.mcpServers in (None, [])


def test_media_buyer_scoped_to_pipeboard():
    agents = build_agents()
    mb = agents["media-buyer"]
    assert mb.mcpServers == ["pipeboard"]
    # tools=None means inherit — we want that so pipeboard MCP tools are usable
    assert mb.tools is None


def test_coordinator_prompt_mentions_key_roles():
    p = COORDINATOR_PROMPT
    assert "scrape_url" in p
    assert "creative-director" in p
    assert "media-buyer" in p
    assert "PAUSED" in p  # safety default must be in prompt
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_agents_smoke.py -v`
Expected: 4 passed (since agents.py and mcp_server.py already exist)

- [ ] **Step 3: Commit**

```bash
git add tests/test_agents_smoke.py
git commit -m "test: agents and coordinator prompt structural invariants"
```

---

## Task 9: Chainlit app (entrypoint + streaming + subagent UI)

**Files:**
- Create: `app.py`
- Create: `chainlit.md`

- [ ] **Step 1: Create chainlit.md (welcome screen)**

```markdown
# Meta Ad Pipeline

Paste a landing URL and I'll:

1. Scrape it to extract ad copy.
2. Design two ad creatives (1080×1080 PNG, hosted on Tigris).
3. Publish them to Meta Ad Manager via pipeboard — PAUSED by default.

After creation you can ask follow-ups like "how is the CTR on that campaign?"
and I'll query Meta live.

**Example prompts**

- `Build a Meta ad for https://acme.example.com, ad account act_123, page 456`
- `How is campaign abc123 doing so far?`
- `Go live on campaign abc123`
```

- [ ] **Step 2: Create app.py**

```python
# app.py
"""Chainlit entrypoint for the Meta ad pipeline."""
import os

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
                    elements=[cl.Image(url=url, name=url.rsplit("/", 1)[-1], display="inline")],
                ).send()

    if current_subagent_step is not None:
        await current_subagent_step.__aexit__(None, None, None)

    if assistant_msg is not None:
        await assistant_msg.update()


def _extract_tigris_urls(text: str) -> list[str]:
    """Find Tigris public PNG URLs in streamed text so the UI can preview them."""
    import re

    pattern = r"https://[a-zA-Z0-9_\-\.]+\.t3\.storage\.dev/creatives/[a-f0-9]+\.png"
    return list(dict.fromkeys(re.findall(pattern, text)))  # preserve order, dedupe
```

- [ ] **Step 3: Verify app.py imports cleanly**

Run: `uv run python -c "import app; print('app imports:', app.MODEL)"`
Expected output: `app imports: claude-opus-4-7`

- [ ] **Step 4: Boot the Chainlit dev server (smoke)**

Copy your `.env.example` to `.env` and fill the required values (at minimum: `BROWSER_USE_API_KEY`, `PIPEBOARD_OAUTH_TOKEN`, Tigris creds, `TIGRIS_BUCKET`). If using a Claude subscription, no `ANTHROPIC_API_KEY` is needed — make sure `claude` CLI is logged in.

Run: `uv run chainlit run app.py --headless --port 8765` in the background for ~5 seconds, then stop.
Expected: server logs `Your app is available at http://localhost:8765` with no import/runtime errors.

Command (foreground):

```bash
uv run chainlit run app.py -w
```

Open `http://localhost:8000` in a browser to see the welcome screen. End smoke with Ctrl+C.

- [ ] **Step 5: Commit**

```bash
git add app.py chainlit.md
git commit -m "feat(app): Chainlit UI with session resume and subagent step nesting"
```

---

## Task 10: Manual integration checklist (documentation only)

**Files:**
- Create: `docs/superpowers/integration-checklist.md`

- [ ] **Step 1: Write the checklist**

```markdown
# Manual Integration Checklist

Run before merging or releasing a new version. Not automated because each step
touches external services with real costs or OAuth tokens.

## Prerequisites

- [ ] `.env` populated with: `BROWSER_USE_API_KEY`, `PIPEBOARD_OAUTH_TOKEN`,
      `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT_URL`,
      `AWS_REGION`, `TIGRIS_BUCKET`. Either `ANTHROPIC_API_KEY` set OR Claude
      Code CLI logged in.
- [ ] Tigris bucket exists and is publicly readable.
- [ ] Pipeboard OAuth token is fresh (obtain via
      `npx @modelcontextprotocol/inspector` against
      `https://meta-ads.mcp.pipeboard.co/`).
- [ ] Sandbox Meta ad account + page ID available.

## Boot smoke

- [ ] `uv run chainlit run app.py -w` starts without import errors.
- [ ] Opening `http://localhost:8000` renders the welcome screen from
      `chainlit.md`.

## Scrape path

- [ ] Send: `Scrape https://example.com for ad copy`.
- [ ] Observe: coordinator calls `scrape_url` (visible as tool call in UI),
      Browser Use Cloud runs, returns structured AdCopy, coordinator
      summarizes fields in chat.

## Creative path

- [ ] Send: `Build a Meta ad for https://example.com, 2 variants, ad account
      act_SANDBOX, page 12345`.
- [ ] Observe:
  - `creative-director` subagent appears as a nested step.
  - Two `render_creative` tool calls complete.
  - Two inline image previews display in the chat (Tigris URLs).
  - PNGs open in a browser and are 1080x1080.

## Publish path

- [ ] Same message as above proceeds to `media-buyer` subagent.
- [ ] `media-buyer` chains pipeboard tool calls: upload/reference image,
      create_campaign (status=PAUSED), create_adset, create_ad_creative,
      create_ad.
- [ ] Final coordinator message contains the campaign_id, adset_id, creative_ids,
      ad_ids.
- [ ] Log in to Meta Ad Manager against the sandbox account: the campaign
      exists, status is PAUSED.

## Analytics path

- [ ] Send: `How is campaign <id> doing?` (use the id from the publish step).
- [ ] Observe: coordinator delegates to `media-buyer`, which calls the
      pipeboard insights tool and returns a summary.

## Go-live path

- [ ] Send: `Go live on campaign <id>`.
- [ ] Observe: `media-buyer` flips status to ACTIVE. Verify in Meta Ad Manager.

## Failure paths

- [ ] Invalid URL: coordinator surfaces the Browser Use Cloud error; no ad is
      created.
- [ ] Malformed HTML from creative-director: `render_creative` returns error;
      subagent retries with a revised variant.
- [ ] Expired pipeboard token: `media-buyer` surfaces 401 and tells the user
      to refresh via the MCP inspector.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/integration-checklist.md
git commit -m "docs: add manual integration checklist"
```

---

## Post-implementation

- [ ] All unit tests pass: `uv run pytest tests/ -v`
- [ ] `.env` is created and gitignored.
- [ ] Run through Task 10 manual checklist end-to-end at least once on a
      sandbox Meta ad account.
- [ ] Confirm Tigris bucket retention policy is acceptable (keep-forever is
      OK for v1).

## Known gaps to revisit

1. **Pipeboard tool discovery.** The `media-buyer` prompt assumes tool names
   from the pipeboard GitHub README. On first run, inspect the server's live
   tool list (via `npx @modelcontextprotocol/inspector` or the SDK's
   `mcp_servers` discovery) and reconcile. Update the prompt if the names
   differ.
2. **URL acceptance on pipeboard.** Task 9 / media-buyer prompt covers both
   paths (upload-from-URL vs direct image_url in creative). Validate which
   works once and pin the prompt to that path.
3. **Variant rendering concurrency.** Current flow renders variants
   sequentially. If it feels slow, wrap the creative-director's render loop
   in `asyncio.gather`. Weasyprint is CPU-bound so expect diminishing returns
   past ~3 in-flight renders.
