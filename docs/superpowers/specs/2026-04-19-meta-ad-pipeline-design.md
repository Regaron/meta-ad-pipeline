# Design: Conversational Meta Ad Pipeline

- **Date:** 2026-04-19
- **Status:** Draft (design approved; awaiting user review before plan phase)
- **Author:** brainstorming session
- **Terminal state of brainstorming:** this spec. Next step: writing-plans.

## 1. Goal

A conversational web application where a user enters a landing URL and Claude delegates to specialized sub-agents to research the page, generate ad creatives, and publish them to Meta Ad Manager. The same conversation handles follow-ups such as "how is that campaign performing?" by delegating to the same media-buyer sub-agent (which also owns analytics tools).

Primary constraint: minimize what we operate. Offload browser automation to Browser Use Cloud, image hosting to Tigris (S3-compatible object storage), ad publishing and analytics to pipeboard.co's hosted MCP, UI and chat infrastructure to Chainlit, and agent orchestration to `claude-agent-sdk`. Local concerns: one Python process plus a CPU-bound rasterize step (`weasyprint`) that runs in-process.

## 2. High-level architecture

```
┌──────────────────────────────────────────────────────────────┐
│  USER (browser)  — Chainlit-hosted chat UI                   │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  app.py   —  Chainlit Python process                         │
│                                                              │
│  on_message → claude_agent_sdk.query(                        │
│     prompt=user_message,                                     │
│     options=ClaudeAgentOptions(                              │
│        resume=chainlit_session_id,                           │
│        system_prompt=COORDINATOR_PROMPT,                     │
│        allowed_tools=["Agent", "mcp__adpipeline__scrape_url"],│
│        agents={creative_director, media_buyer},             │
│        mcp_servers={pipeboard (hosted URL), local (SDK)},    │
│        model="claude-opus-4-7",                              │
│        ...                                                   │
│     ))                                                       │
│                                                              │
│  Chainlit data layer persists chat history (SQLite).         │
│  No local artifact files; PNGs live on Tigris only.          │
└──────────┬────────────────┬─────────────────┬────────────────┘
           │                │                 │                │
           ▼                ▼                 ▼                ▼
   ┌──────────────┐ ┌────────────────┐ ┌──────────────┐ ┌───────────────────┐
   │ Browser Use  │ │ weasyprint     │ │ Tigris       │ │ pipeboard.co MCP  │
   │ Cloud SDK    │ │ (HTML+SVG+CSS  │ │ (S3-compat   │ │ (hosted OAuth URL)│
   │ URL→AdCopy   │ │ → PNG bytes,   │ │ object store │ │ Meta Ads +        │
   │              │ │  in-memory)    │ │  via boto3)  │ │ insights — URL-   │
   │              │ │                │ │  → public URL│ │ based creatives   │
   └──────────────┘ └────────────────┘ └──────────────┘ └───────────────────┘
```

One Python process, one deploy target. No FastAPI, no Next.js, no Docker compose, no custom SQL schema, no local image retention.

## 3. Components

### 3.1 Chainlit app (`app.py`)

- Single entrypoint with `@cl.on_chat_start` and `@cl.on_message` handlers.
- First message of a conversation: calls `query()` without `resume`; captures the SDK session id from the init `SystemMessage` and stores it via `cl.user_session`. Subsequent messages pass `resume=stored_sdk_session_id` so Claude Agent SDK's own conversation context survives across user turns.
- Streams `SDKAssistantMessage` text blocks to the chat as they arrive.
- Detects subagent invocations via `parent_tool_use_id` and displays a nested "step" in the UI so the user sees which sub-agent is running.
- When the creative-director sub-agent finishes, displays the generated PNGs inline using `cl.Image`.

### 3.2 Coordinator (implicit)

Not a separate `AgentDefinition`. It is the top-level `query(...)` with a coordinator system prompt:

> "You are an ad-campaign assistant. When the user asks you to build an ad for a URL, call `scrape_url` to get the ad copy, then delegate to `creative-director` for images, then `media-buyer` to publish. When the user asks about performance or existing campaigns, delegate to `media-buyer`. Never render or publish yourself — always delegate those to the right subagent."

Coordinator's `allowed_tools` is `["Agent", "scrape_url"]` (MCP-scoped form: `"mcp__adpipeline__scrape_url"`).

`scrape_url` stays at the coordinator level because Browser Use Cloud is itself a Claude-driven agent loop — wrapping it in a local subagent that just calls one tool adds a layer with no extra value. The structured `AdCopy` it returns is small enough that keeping it in the coordinator's context has no caching downside.

### 3.3 Sub-agents

Defined programmatically as `AgentDefinition` entries on the coordinator's `ClaudeAgentOptions.agents`. Two subagents — the browsing step runs directly via Browser Use Cloud and does not need a local subagent wrapper.

#### `creative-director`

- **Purpose:** Generate N ad-creative variants as HTML documents (inline CSS + inline SVG), rendering each to a 1080×1080 PNG via one tool call per variant.
- **Tools:** `["render_creative"]`.
- **No MCP servers scoped.**
- **Prompt:** Rules for 1080×1080, self-contained HTML (inline `<style>` + inline `<svg>`, no external images, Google Fonts allowed via `<link>`), strong visual hierarchy, CTA as a styled button-like element. For each variant: compose HTML and call `render_creative`.
- **Model:** `"inherit"`.

#### `media-buyer`

- **Purpose:** Publish ads via pipeboard and answer performance / analytics questions.
- **Tools:** All pipeboard MCP tools (upload image, create campaign/ad set/creative/ad, list/describe campaigns, get insights).
- **MCP servers scoped:** `["pipeboard"]`.
- **Prompt:** Goal-oriented. Default new ads to `PAUSED` unless the user has explicitly said "go live". For performance questions, call the correct insights tool and summarize briefly.
- **Model:** `"inherit"`.

### 3.4 Custom tools (in-process MCP server)

Registered once via `create_sdk_mcp_server`. Two tools:

| Tool | Purpose | Used by |
|---|---|---|
| `scrape_url(url, extraction_goal) -> AdCopy` | Thin async wrapper over `AsyncBrowserUse().run(task, output_schema=AdCopy, model="claude-opus-4.6")`. | **coordinator** (top-level) |
| `render_creative(html, variant_note) -> {variant_id, png_url}` | Render HTML to PDF bytes with `weasyprint`, rasterize page 1 to PNG bytes with `pypdfium2` at 1080×1080, upload PNG to Tigris via boto3 with `ACL=public-read`, return the public URL. | `creative-director` |

Rendered PNGs are never retained on local disk — they go through `weasyprint.HTML(string=html).write_pdf()` (returns PDF bytes), then `pypdfium2.PdfDocument(io.BytesIO(pdf_bytes))[0].render(scale=...).to_pil().save(buf, "PNG")` (PIL bytes), then `s3.put_object(Body=bytes, ACL="public-read")` to Tigris. HTML source is not persisted; it lives only in the tool-call trace (visible in Chainlit's UI and in the SDK transcript).

> Naming: once registered via `create_sdk_mcp_server(name="adpipeline", tools=[...])`, Claude Agent SDK exposes these tools to subagents with the MCP-scoped form `mcp__adpipeline__<tool_name>` (that is the string used in `AgentDefinition.tools`). The short names above are the logical identifiers used throughout this spec for readability.

### 3.5 Tigris object storage (S3-compatible)

- **Purpose:** Host rendered ad creatives at public HTTPS URLs so Meta can fetch them directly. No local PNG files.
- **Client:** `boto3` with `endpoint_url="https://t3.storage.dev"`, virtual-hosted addressing, `region="auto"`. (Native Tigris Python SDK not required; boto3 is the recommended path for S3-compatible workflows.)
- **Auth:** standard AWS env vars — `AWS_ACCESS_KEY_ID` (`tid_...`), `AWS_SECRET_ACCESS_KEY` (`tsec_...`), `AWS_ENDPOINT_URL`, `AWS_REGION=auto`. Bucket name via `TIGRIS_BUCKET` (our own env var).
- **Object key convention:** `creatives/{session_id}/{variant_id}.png`.
- **ACL:** `public-read` (Meta's API will fetch the URL without auth).
- **Public URL format:** `https://{bucket}.t3.storage.dev/creatives/{session_id}/{variant_id}.png` (virtual-hosted).
- **`upload_png(bytes, key) -> url`** helper lives inline in `tools/creative.py` (it's a ~10-line wrapper with a single caller; no dedicated module).

### 3.6 pipeboard MCP (hosted)

- Transport: URL (Streamable HTTP).
- Endpoint: `https://meta-ads.mcp.pipeboard.co/`.
- Auth: OAuth 2.0 bearer token (`PIPEBOARD_OAUTH_TOKEN`), obtained once via the MCP inspector (`npx @modelcontextprotocol/inspector`) and stored in `.env`.
- Exposed as `{name: "pipeboard", type: "url", url, authorization_token}` on `ClaudeAgentOptions.mcp_servers`.
- Scoped to `media-buyer` only by listing `mcp_servers=["pipeboard"]` on that `AgentDefinition`.
- Relied-on tools (per the pipeboard GitHub repo): `mcp_meta_ads_upload_ad_image`, `mcp_meta_ads_create_campaign`, `mcp_meta_ads_create_adset`, `mcp_meta_ads_create_ad_creative`, `mcp_meta_ads_create_ad`, plus insights/list tools for analytics.
- **URL-only creative flow.** The media-buyer never sees local file paths. Its inputs for ad creation are Tigris URLs (from `render_creative` outputs). Two viable paths exist on the Meta API; whichever pipeboard surfaces best is the one we use — see §12 open item:
  1. Upload-from-URL: if `mcp_meta_ads_upload_ad_image` (or a sibling tool) accepts `image_url`, pass the Tigris URL; pipeboard returns an image hash; use the hash in the creative.
  2. Direct-URL in creative: pass the Tigris URL as `image_url` in `mcp_meta_ads_create_ad_creative` `link_data`; Meta fetches the image itself and derives a hash internally.

### 3.7 Rendering

Single unified format: **HTML with inline `<style>` and inline `<svg>`**. Two-stage pure-Python render at 1080×1080:

1. `weasyprint.HTML(string=html, ...).write_pdf()` → PDF bytes. WeasyPrint handles CSS layout, inline SVG, Google Fonts via `<link>`, gradients/shapes. The input HTML sets `@page { size: 1080px 1080px; margin: 0 }` so the PDF has a single 1080×1080 page.
2. `pypdfium2.PdfDocument(io.BytesIO(pdf)).pages[0].render(scale=1.0).to_pil()` → PIL `Image` → `PNG` bytes via `save()`.

Resulting PNG bytes go straight to `s3.put_object(Bucket=..., Key=..., Body=bytes, ACL="public-read", ContentType="image/png")`.

Rationale: pure Python, no Chromium, no system deps beyond what pip installs. WeasyPrint's PNG output was removed in v53 (2021); going through PDF is the supported replacement. `pypdfium2` ships pre-built PDFium wheels for all platforms.

## 4. Data flow — single end-to-end turn

Example: user types "Build a Meta ad for https://acme.com with 2 variants, ad account act_123, page 456".

1. Chainlit invokes `on_message` with the prompt.
2. Handler calls `claude_agent_sdk.query(prompt=..., options=ClaudeAgentOptions(resume=session_id, ...))`.
3. Coordinator reasons, calls `scrape_url("https://acme.com", extraction_goal="...")` directly; the tool invokes Browser Use Cloud; validated `AdCopy` lands in the coordinator's context.
4. (no step — the scrape was the coordinator's own tool call.)
5. Coordinator invokes `Agent(subagent_type="creative-director", prompt="Generate 2 variants using this copy: {...}")`.
6. `creative-director` composes two HTML variants (different `variant_note`s) and calls `render_creative` once per variant; each call rasterizes to bytes + uploads to Tigris; subagent returns `{variant_ids, png_urls}`.
7. Chainlit displays the PNGs inline by URL via `cl.Image(url=png_url)` (detected via `parent_tool_use_id` path).
8. Coordinator invokes `Agent(subagent_type="media-buyer", prompt="Publish these creatives to act_123 / page 456. Image URLs: [...]. Status: PAUSED.")`.
9. `media-buyer` uses pipeboard MCP tools with **Tigris URLs** (never local paths) to create campaign/adset/creatives/ads; returns created IDs.
10. Coordinator's final message summarizes: "Created paused campaign `abc` with 2 ads. Review in Meta Ad Manager and reply `go live X` to activate."

A follow-up turn ("how is the CTR on campaign abc?") resumes the same session, coordinator delegates directly to `media-buyer`, which calls pipeboard's insights tool and returns a summary.

## 5. State persistence

- **Chat UI state:** Chainlit's built-in data layer (SQLite by default) persists the user-visible chat history. No custom schema.
- **Agent SDK session state:** the Claude Agent SDK maintains its own transcript files keyed by the SDK session id. We capture that id from the first `SystemMessage` of each Chainlit conversation, store it in `cl.user_session`, and pass it as `resume=...` on every subsequent `query()` call. Subagent transcripts live in their own SDK files.
- **Rendered creatives (PNG):** Tigris only. Public URLs are the authoritative reference.
- **Campaigns:** no local index. When the user asks about an existing campaign, the media-buyer agent queries pipeboard live.

Nothing else is persisted locally. No `data/` directory, no scrape cache, no HTML source files — HTML exists only in the live SDK transcript.

## 6. Error handling / failure modes

| Failure | Handling |
|---|---|
| Browser Use Cloud task fails or times out | `scrape_url` tool raises; coordinator sees the error in its own context and can retry with a refined `extraction_goal` or report to user. |
| `AdCopy` validation (Pydantic) fails | `scrape_url` re-raises; coordinator can retry once with a sharper extraction task, then report failure if still failing. |
| `weasyprint` render error | `render_creative` tool returns `{variant_id, error}`; creative director composes a revised variant and calls `render_creative` again. |
| Tigris upload error (network, ACL, bucket missing) | `render_creative` tool returns `{variant_id, error}`; creative director surfaces; coordinator reports to user (likely config issue). |
| pipeboard MCP 401 / auth error | Media-buyer reports auth failure; coordinator instructs user to refresh `PIPEBOARD_OAUTH_TOKEN` via the MCP inspector. |
| Meta can't fetch the Tigris URL (e.g. ACL mis-set, private object) | pipeboard returns Meta's "failed to download image" error; media-buyer reports; fix is ACL on the uploaded object. |
| Meta API error (e.g., ad policy rejection) | Surfaced via pipeboard's tool result; media-buyer summarizes which ad failed and why. |
| Anthropic API 429 / 5xx | Handled by Claude Agent SDK's built-in retries. |

Safety default: any ad create/update call from the `media-buyer` subagent uses `status="PAUSED"` unless the user's message explicitly contains "go live" (or equivalent) in the context.

## 7. Testing

- `tests/test_scrape.py` — mock Browser Use Cloud client; assert `scrape_url` returns a validated `AdCopy` from a canned response.
- `tests/test_render.py` — feed fixture HTML through `render_creative`; assert weasyprint produces PNG bytes at 1080×1080 (via Pillow), and assert boto3 (stubbed with `moto`) received `put_object` with correct bucket, key, `ACL="public-read"`, `ContentType="image/png"`. Assert the returned URL matches the virtual-hosted format.
- `tests/test_agents_smoke.py` — offline smoke test with a monkey-patched `query` that asserts the coordinator calls `scrape_url` first, then delegates to `creative-director`, then `media-buyer`, given a canonical prompt.
- Manual integration checklist (not automated): end-to-end run against a sandbox ad account and a dev Tigris bucket.

## 8. File layout

```
.
├── pyproject.toml
├── .env.example
├── chainlit.md                      # Chainlit welcome screen content
├── app.py                           # Chainlit entrypoint
├── agents.py                        # COORDINATOR_PROMPT + 2 AgentDefinitions
├── tools/
│   ├── __init__.py
│   ├── mcp_server.py                # create_sdk_mcp_server + registration
│   ├── scrape.py                    # scrape_url tool
│   ├── creative.py                  # render_creative tool (weasyprint + boto3 upload inlined)
│   └── schemas.py                   # AdCopy, RenderedCreative
└── tests/
    ├── test_scrape.py
    ├── test_render.py
    └── test_agents_smoke.py
```

Obsolete files from the prior Python-only design (`coordinator.py`, `browser_agent.py`, `image_agent.py`, `ads_agent.py`, `signatures.py` in repo root) will be removed during implementation.

## 9. Dependencies

**Python (`pyproject.toml`):**

- `claude-agent-sdk>=0.2.111` (Opus 4.7 requires this version or later)
- `browser-use-sdk>=3.0`
- `weasyprint>=62` (HTML/CSS/SVG → PDF)
- `pypdfium2>=4` (PDF → PNG, pure-Python bindings)
- `pillow>=11` (PIL Image → PNG bytes in render path)
- `chainlit>=2.0`
- `boto3>=1.35` (Tigris S3-compatible upload)
- `pydantic>=2.9`
- `python-dotenv>=1.0`

Dev:

- `pytest>=8`
- `pytest-asyncio>=0.24`
- `moto>=5` (S3 mocking)

**Environment:**

- **Anthropic auth** — one of:
  - **Claude subscription (local dev only)** — have Claude Code CLI installed and logged in (`claude` OAuth). Agent SDK uses the stored token automatically; no env var needed. Rate limits follow your Pro/Max tier. Must not be used for multi-user hosting per Anthropic terms.
  - **`ANTHROPIC_API_KEY`** — required for any non-local deployment or any multi-user scenario.
- `BROWSER_USE_API_KEY` (from Browser Use Cloud, `bu_...`)
- `PIPEBOARD_OAUTH_TOKEN` (from MCP inspector against `https://meta-ads.mcp.pipeboard.co/`)
- **Tigris** (S3-compatible):
  - `AWS_ACCESS_KEY_ID` (`tid_...`)
  - `AWS_SECRET_ACCESS_KEY` (`tsec_...`)
  - `AWS_ENDPOINT_URL=https://t3.storage.dev`
  - `AWS_REGION=auto`
  - `TIGRIS_BUCKET` (bucket name, our convention)

## 10. Model selection

- **Coordinator + all sub-agents:** `claude-opus-4-7` with adaptive thinking. Controlled via `claude-agent-sdk` options, which set the Anthropic SDK params directly — no temperature/top_p issues.
- **Browser Use Cloud:** `claude-opus-4.6` (their supported model list uses `.` not `-`; Opus 4.7 not listed as supported).
- **No DSPy, no LiteLLM.** Agent loops are native `claude-agent-sdk`; sub-step reasoning stays inside each sub-agent.

## 11. Out of scope (for v1)

- Multi-tenant auth. App assumes a single operator; Chainlit's built-in auth adapters can be added later if needed.
- Scheduling. No cron or batch. Every interaction is chat-driven.
- Cost ceilings / budget guards beyond the PAUSED default.
- Non-Meta ad platforms.
- Historical analytics rollups. All insights are live pipeboard calls.
- Custom UI beyond Chainlit's defaults.

## 12. Open items (resolve in implementation plan or later)

- Confirm exact pipeboard tool names by inspecting the server via MCP inspector on first run (GitHub repo listed tool names should match but the ground truth is the server's `tools/list`).
- Confirm how pipeboard accepts Tigris URLs: (a) upload-from-URL tool variant, or (b) direct `image_url` in `create_ad_creative`. Validate at implementation time via MCP inspector — whichever works, the media-buyer prompt names that path explicitly.
- Decide concurrency for multiple variants: call `render_creative` variants in sequence vs. `asyncio.gather`. Weasyprint is thread-safe but CPU-bound; sequential is likely fine for N≤4. Tigris uploads are I/O-bound — `asyncio.gather` there is a win if we parallelize.
- Decide on Tigris object retention / cleanup policy. Keep-forever is acceptable for v1; add a scheduled cleanup later if cost pressure arises.
