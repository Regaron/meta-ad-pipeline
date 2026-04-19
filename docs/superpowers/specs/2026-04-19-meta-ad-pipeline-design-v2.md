# Design v2: Conversational Meta Ad Pipeline — refinements

- **Date:** 2026-04-19
- **Status:** Draft (post-brainstorm refinements; supersedes the changed sections of v1)
- **Layered on:** [`2026-04-19-meta-ad-pipeline-design.md`](./2026-04-19-meta-ad-pipeline-design.md) (v1)
- **Implementation status:** worktree `meta-ad-pipeline` is mid-Task-2 of the v1 plan. Untracked `pyproject.toml`, `uv.lock`, `.env.example` reflect v1; they will be amended before commit.
- **Author:** brainstorming session, follow-up
- **Terminal state of brainstorming:** this spec. Next step: writing-plans (delta plan that amends the v1 plan).

## 1. Why a v2

Two new inputs landed after v1 was written:

1. A richer **Browser Use research prompt** that extracts brand identity (logo, hex colors), 3 reference image URLs, tone, CTA, and one drafted Problem/Solution copy variant (Hook + Body + Headline ≤40 chars).
2. A **revised Tigris setup**: a different bucket, distinct env var namespace, and the `t3.tigrisfiles.io` CDN domain for public URLs.

Two design intents emerged from brainstorming:

- Keep the **synthetic creative pathway** (HTML/CSS/SVG → weasyprint → PNG). When scraped brand photos are available, creative-director uses them as **visual reference** to inform its design — it does not embed them directly.
- Be **as agentic as possible**: don't pre-bake field mapping rules. Pass the rich research downstream and let the agents (creative-director, media-buyer) decide how to spend it.

Everything else in v1 stays: Chainlit single-process app, top-level coordinator + 2 subagents, in-process MCP server, weasyprint→pypdfium2→PIL render pipeline, pipeboard MCP integration, PAUSED-by-default safety, no local state beyond Chainlit's data layer.

## 2. Delta summary

| Area | v1 | v2 |
|---|---|---|
| Scraper output schema | `AdCopy` (headline, primary_text, description, value_props, call_to_action enum, brand_color_theme phrase) | `BrandResearch` (rich: identity, value prop, visual assets, tone, CTA literal, draft creative copy) |
| Browser Use prompt | "Extract ad copy" — produces Meta-shaped fields directly | Performance-marketing research prompt — produces source material; mapping happens later, agentically |
| Field → Meta mapping | Pre-baked: AdCopy fields map 1:1 to Meta fields | Agentic: media-buyer composes Meta `headline`/`primary_text`/`description`/CTA enum from `BrandResearch` |
| CTA enum | Scraper emits enum directly | Scraper emits literal button text; media-buyer picks the Meta enum from context |
| Creative-director input | `AdCopy` only | `BrandResearch` + can call `view_brand_reference(url)` to *see* scraped photos before composing HTML |
| Creative-director copy variation | One copy, multiple visuals (implicit) | Same — explicitly: all visual variants share the scraped Hook/Body/Headline; only visuals diverge |
| Storage env vars | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_ENDPOINT_URL` / `AWS_REGION` | `TIGRIS_STORAGE_ACCESS_KEY_ID` / `TIGRIS_STORAGE_SECRET_ACCESS_KEY` / `TIGRIS_STORAGE_BUCKET` (mapped to boto3 internally) |
| Storage bucket | `ad-pipeline-creatives` | `ad-images-tigris` |
| Storage public URL | `https://{bucket}.t3.storage.dev/{key}` | `https://{bucket}.t3.tigrisfiles.io/{key}` (Tigris global CDN) |
| Storage API endpoint | `https://t3.storage.dev` | unchanged — `https://t3.storage.dev` is still the S3-compatible API endpoint; only the public-read URL changes |
| Variant count | hardcoded default 2 | coordinator picks 1–4 from user phrasing; default 2 when unspecified |
| Meta campaign objective | hardcoded `OUTCOME_TRAFFIC` | media-buyer picks from `OUTCOME_TRAFFIC` / `OUTCOME_SALES` / `OUTCOME_LEADS` / `OUTCOME_AWARENESS` / `OUTCOME_ENGAGEMENT` based on `BrandResearch` |
| Meta adset targeting | hardcoded broad US 18–65 | media-buyer infers age range, geo, and 2–4 interest tags from `value_prop` + `tone_adjectives` |
| Meta optimization goal + billing event | hardcoded `LINK_CLICKS` / `IMPRESSIONS` | media-buyer picks the pair that matches its chosen objective |
| Meta daily budget | hardcoded $10/day | media-buyer picks `$5–$50/day` from inferred scale; **hard ceiling $50/day** unless user explicitly authorizes higher in the same turn |

Tools (`tools/mcp_server.py`) gain one tool: `view_brand_reference`. The other two (`scrape_url`, `render_creative`) keep their names but `scrape_url`'s output schema and Browser Use prompt change; `render_creative`'s upload helper changes to emit the new public URL.

## 3. Scraper (`tools/scrape.py`)

### 3.1 New schema: `BrandResearch`

Replaces `AdCopy` in `tools/schemas.py`. Mirrors the user-supplied Browser Use prompt 1:1 so the model has no translation surface to get wrong.

```python
class BrandIdentity(BaseModel):
    logo_url: str | None  # null if no usable logo found
    primary_color_hexes: list[str]  # 1-3 hex codes, e.g. ["#0F62FE", "#161616"]

class CoreValueProp(BaseModel):
    headline: str  # the site's main headline, verbatim
    top_3_benefits: list[str]  # 3 short benefit statements

class CreativeCopyIdea(BaseModel):
    """One Problem/Solution ad copy draft."""
    hook: str       # relatable pain point
    body: str       # how the product solves it
    headline: str   # punchy, benefit-driven, ≤40 chars

class BrandResearch(BaseModel):
    source_url: str
    identity: BrandIdentity
    value_prop: CoreValueProp
    visual_asset_urls: list[str]   # up to 3 high-quality product/lifestyle image URLs; may be empty
    tone_adjectives: list[str]     # exactly 3 adjectives describing voice
    cta_button_text: str           # the literal primary CTA text on the page, e.g. "Get Started"
    creative_copy_idea: CreativeCopyIdea
```

Notes:
- All fields are required except `identity.logo_url` (null OK) and `visual_asset_urls` (may be empty if the page has no usable images).
- No CTA enum at this layer. The literal button text is preserved verbatim. The Meta enum decision is made later, in context.
- The schema is intentionally flat-ish — nested only where it improves model accuracy (the model produces better hex codes when they're tied to "identity", and writes better Hook/Body when they're tied to "creative_copy_idea").
- Pydantic constraints to enforce in the implementation: `tone_adjectives` `min_length=3, max_length=3`; `visual_asset_urls` `max_length=3`; `value_prop.top_3_benefits` `min_length=3, max_length=3`; `identity.primary_color_hexes` `min_length=1, max_length=3`; hex codes match `^#[0-9A-Fa-f]{3,8}$`. These shape constraints are what makes Browser Use Cloud's structured-output path reliable.

### 3.2 Browser Use prompt

The `scrape_url` tool wraps `AsyncBrowserUse().run(...)` and uses the user's research prompt verbatim, with `BrandResearch` as `output_schema`:

```
Navigate to {url} and act as a performance marketing researcher.
- Brand Identity: find the primary logo URL and 1–3 hex codes for the brand's primary colors.
- Core Value Prop: extract the main headline and the top 3 benefits the product provides.
- Visual Assets: collect URLs for up to 3 high-quality images (product or lifestyle) suitable for ads. Empty list is acceptable if none are usable.
- Tone of Voice: describe the brand's writing style in exactly 3 adjectives.
- Call to Action: identify the primary button text on the page (e.g., "Get Started"). Do NOT translate it — return the literal text.
- Creative Copy Idea: based on the site's content, write one Problem/Solution ad copy variant: Hook (a relatable pain point), Body (how this product solves it), Headline (punchy, benefit-driven, max 40 chars).
Additional focus: {extraction_goal}
Return all findings as a single BrandResearch JSON object.
```

`extraction_goal` remains a runtime argument so the coordinator can append per-call hints (e.g., "focus on the B2B persona").

### 3.3 Tool signature

```python
scrape_url(url: str, extraction_goal: str) -> {"content": [{"type": "text", "text": <BrandResearch JSON>}]}
```

Same shape as v1; the `text` payload is now a `BrandResearch.model_dump_json()` instead of `AdCopy.model_dump_json()`.

## 4. Storage (`tools/creative.py`)

### 4.1 Env vars

We adopt the user's namespace internally and translate to boto3 at the boundary:

| App env var | boto3 expects | Notes |
|---|---|---|
| `TIGRIS_STORAGE_ACCESS_KEY_ID` | `AWS_ACCESS_KEY_ID` | translated by `_s3_client()` |
| `TIGRIS_STORAGE_SECRET_ACCESS_KEY` | `AWS_SECRET_ACCESS_KEY` | translated by `_s3_client()` |
| `TIGRIS_STORAGE_BUCKET` | `Bucket` arg | bucket name only, default `ad-images-tigris` |
| `TIGRIS_API_ENDPOINT` (optional, default `https://t3.storage.dev`) | `endpoint_url` arg | S3-compatible API endpoint |
| `TIGRIS_PUBLIC_HOST` (optional, default `t3.tigrisfiles.io`) | URL synthesis | base host for the *public* URL we hand to Meta |

The `_s3_client()` helper reads `TIGRIS_STORAGE_*` and passes them explicitly to `boto3.client("s3", ...)`. We don't pollute the process's `AWS_*` env namespace; this also means `boto3` won't accidentally use ambient AWS creds if the operator has them.

### 4.2 Public URL synthesis

Old: `https://{bucket}.t3.storage.dev/{key}`
New: `https://{bucket}.t3.tigrisfiles.io/{key}` — Tigris's global CDN domain.

Concretely: `https://ad-images-tigris.t3.tigrisfiles.io/creatives/{session_id}/{variant_id}.png`.

The API endpoint stays at `t3.storage.dev` for upload; the public URL we *return* and hand to Meta lives under `t3.tigrisfiles.io`. This split matches Tigris's recommended deployment.

### 4.3 Object key convention

Unchanged from v1: `creatives/{session_id}/{variant_id}.png`.

### 4.4 ACL

Unchanged: `public-read`.

## 5. Creative-director subagent

### 5.1 New input: `BrandResearch`

Coordinator delegates with a prompt that includes the full `BrandResearch` JSON inline, plus the desired number of variants. The subagent does not need to mutate or interpret `creative_copy_idea` for design — copy is fixed across visual variants per Q3 (option A).

### 5.2 New tool: `view_brand_reference(url)`

Subagent prompt invocation in claude-agent-sdk is text-only. To let the creative-director *see* a scraped photo before composing HTML, we add a dedicated tool registered on the same in-process MCP server:

```python
view_brand_reference(url: str) -> {
    "content": [
        {"type": "image", "source": {"type": "url", "url": url}}
    ]
}
```

The tool returns an Anthropic-API-shaped image content block. When the model receives the tool result, it processes the image natively via vision. No download/base64 encoding is required — the Anthropic API's `image_url` source type handles HTTPS URLs directly.

Validation in the tool body:
- HTTPS only (reject `http://`, `file://`, etc.).
- Must end in a common image extension (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`) **or** the host must respond `Content-Type: image/*` on a `HEAD` request. We do the cheap suffix check inline; on miss, do one `HEAD` with a 3-second timeout. Reject anything else.

This is the **only** new tool. It is scoped to creative-director only.

### 5.3 Updated prompt (creative-director)

```
You are a creative director producing Facebook/Meta ad creatives.

You will receive:
  - A BrandResearch JSON with identity (logo_url, primary_color_hexes), value_prop,
    visual_asset_urls, tone_adjectives, cta_button_text, and creative_copy_idea
    (hook, body, headline).
  - A desired number of visual variants (default 2).

Workflow:
  1. If BrandResearch.visual_asset_urls is non-empty, call view_brand_reference once
     per URL (up to 3) to see what the brand's existing photography looks like.
     Use the imagery as inspiration for color, mood, and composition — do NOT embed
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
```

### 5.4 Tools list

```python
tools=[
    "mcp__adpipeline__render_creative",
    "mcp__adpipeline__view_brand_reference",
],
```

## 6. Media-buyer subagent

### 6.1 Same role, fully agentic composition

Media-buyer continues to own publishing and analytics. Two changes:

1. Its **input contract** moves from a pre-shaped `AdCopy` to the full `BrandResearch` JSON plus the `png_urls`, `landing_url`, `ad_account_id`, `page_id`, requested `status`, and optional `budget_override`.
2. Its **prompt scope** widens. Per the "as agentic as possible" directive, the prompt instructs media-buyer to compose, in context:
   - **Ad copy fields** (`headline`, `primary_text`, `description`, `call_to_action` enum) from `BrandResearch.creative_copy_idea` and `BrandResearch.cta_button_text` — guidelines, not rigid mappings, so it can shorten / reorder / re-pick when needed.
   - **Campaign objective** from `BrandResearch.value_prop` and the apparent business model.
   - **Optimization goal + billing event** matched to the chosen objective.
   - **Daily budget** in $5–$50/day, capped at $50/day unless `budget_override` is set.
   - **Targeting** (geo, age range, interest tags) inferred from `tone_adjectives` and `value_prop`.

§6.2 has the full prompt; §6.3 covers budget-cap safety.

### 6.2 Updated prompt (media-buyer) — relevant section only

The publishing block changes from "you receive AdCopy fields" to a fully agentic composition:

```
(A) Publishing. When asked to publish ads, you receive:
      - landing_url, ad_account_id, page_id
      - BrandResearch JSON with all source material
      - A list of png_urls (Tigris public URLs for creative images)
      - status (PAUSED or ACTIVE)
      - optional budget_override (USD/day) if the user explicitly set one

    Compose the Meta ad fields from BrandResearch:
      - headline: usually creative_copy_idea.headline; shorten if >40 chars.
      - primary_text: combine creative_copy_idea.hook and creative_copy_idea.body
        with a blank line between. Trim to ≤125 chars if needed; prefer keeping the
        full body and dropping/compressing the hook.
      - description: pick the strongest of value_prop.top_3_benefits and trim to
        ≤30 chars.
      - call_to_action: map cta_button_text to the closest Meta enum (LEARN_MORE,
        SHOP_NOW, SIGN_UP, DOWNLOAD, GET_OFFER, BOOK_TRAVEL, CONTACT_US,
        SUBSCRIBE) using the brand's intent as context. Default LEARN_MORE when
        unclear.

    Compose the campaign + adset parameters from context (NOT from defaults):
      - objective: pick ONE of OUTCOME_TRAFFIC, OUTCOME_SALES, OUTCOME_LEADS,
        OUTCOME_AWARENESS, OUTCOME_ENGAGEMENT based on what the brand sells.
        Ecommerce/checkout pages → OUTCOME_SALES. Lead-gen / B2B / form-based →
        OUTCOME_LEADS. Content / blog / community → OUTCOME_ENGAGEMENT. Pure
        traffic / "go visit our site" → OUTCOME_TRAFFIC. Brand awareness with no
        conversion goal → OUTCOME_AWARENESS.
      - optimization_goal + billing_event: pick the pair that matches the
        objective per Meta's matrix (e.g., OUTCOME_TRAFFIC → LINK_CLICKS /
        IMPRESSIONS; OUTCOME_SALES → OFFSITE_CONVERSIONS / IMPRESSIONS;
        OUTCOME_LEADS → LEAD_GENERATION / IMPRESSIONS; OUTCOME_ENGAGEMENT →
        POST_ENGAGEMENT / IMPRESSIONS; OUTCOME_AWARENESS → REACH /
        IMPRESSIONS). When unsure, default to LINK_CLICKS / IMPRESSIONS.
      - daily_budget_cents: pick a sensible test budget in the range $5–$50/day
        (i.e., 500–5000 cents) based on the brand's apparent scale and the
        number of variants. SAFETY CEILING: never exceed $50/day unless
        budget_override is set; if the user did not explicitly authorize a
        higher number in their request, cap at 5000 cents and note in the
        return payload that the cap was applied.
      - targeting: infer from BrandResearch. Geo defaults to US unless the
        brand is clearly regional (e.g., a UK-only retailer → GB). Age range:
        narrow from 18–65 toward the persona implied by tone_adjectives and
        value_prop (e.g., "playful, bold, viral" → 18–34; "professional,
        enterprise, ROI" → 28–55). Add 2–4 Meta interest targeting tags
        derived from value_prop.top_3_benefits.

    Then sequence: discover the upload-from-URL tool vs direct image_url path
    (one-time tool-list inspection), create_campaign with the chosen objective
    and requested status, create_adset with the chosen targeting/budget/
    optimization, create_ad_creative per png_url, create_ad per creative.
    Return JSON {campaign_id, adset_id, creative_ids, ad_ids, objective,
    daily_budget_cents, targeting_summary, budget_cap_applied, notes}.
```

Analytics block (B) and the safety default (PAUSED unless "go live") are unchanged from v1.

### 6.3 Safety: budget cap and authorization phrases

The $50/day ceiling is enforced inside the media-buyer prompt rather than as a hard validation in code, because the user can override it conversationally. Recognized authorization phrases the coordinator passes through as `budget_override`:

- `"set budget $X/day"` (any number)
- `"X dollars per day"` / `"X/day"`
- `"budget X"` after the user said "go live" in the same turn

If the user says only `"go live"` without a budget, the budget remains capped at $50/day (status flip alone does not unlock spend).

## 7. Coordinator prompt

Updated to reflect the `BrandResearch` payload and the new agentic decisions the coordinator makes upfront:

```
You are an ad-campaign assistant for Facebook/Meta advertising.

When the user asks you to build an ad for a URL:
  1. Decide variant_count from the user's phrasing:
     - "quick test", "just one", "single creative" → 1
     - no qualifier, normal request → 2 (default)
     - "test a bunch", "creative bake-off", "multiple angles" → 3 or 4 (cap 4)
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
optimization goal, or budget into your delegation prompt — pass the source
material and let media-buyer decide.
```

## 8. Things that do NOT change from v1

For the avoidance of doubt — these v1 sections still apply verbatim and should not be re-read against this v2:

- High-level architecture diagram and one-process deployment shape (§2).
- Chainlit `app.py` structure: `on_chat_start`, `on_message`, session resume, streaming, parent_tool_use_id-based subagent step nesting, inline image previews (§3.1).
- Coordinator-as-implicit shape (no `AgentDefinition` for it; it's the top-level `query()`); `scrape_url` stays at the coordinator level (§3.2).
- Two-subagent split: creative-director and media-buyer (§3.3); pipeboard MCP scoped to media-buyer (§3.6).
- In-process MCP server pattern via `create_sdk_mcp_server`; `mcp__adpipeline__*` naming convention (§3.4).
- Render pipeline: `weasyprint.HTML(...).write_pdf()` → `pypdfium2.PdfDocument(...)` → PIL → PNG bytes; rationale and version constraints (§3.7, §9).
- Data flow shape (§4) — only the schema name flowing through the steps changes (`AdCopy` → `BrandResearch`).
- State persistence model (§5).
- Error-handling table (§6) — replace mentions of `AdCopy` with `BrandResearch` but the failure modes are otherwise identical.
- Testing strategy (§7) — adapted in §10 below.
- File layout (§8) — additions only (no new modules).
- Dependencies (§9) — unchanged.
- Model selection (§10) — unchanged.
- Out of scope (§11) and v1 open items (§12) still hold.

## 9. Implementation impact (delta against the v1 plan)

The v1 plan (`docs/superpowers/plans/2026-04-19-meta-ad-pipeline.md`) has 10 tasks. Disposition:

| v1 Task | Status | v2 disposition |
|---|---|---|
| 1. Clean up obsolete files + .gitignore | Done in worktree (commit `dddc103`) | Keep |
| 2. pyproject.toml + .env.example | Untracked (mid-task) | **Amend before commit:** `.env.example` uses `TIGRIS_STORAGE_*` not `AWS_*`; bucket = `ad-images-tigris`. `pyproject.toml` unchanged. |
| 3. Pydantic schemas (`AdCopy`, `RenderedCreative`) | Not started | **Replace `AdCopy` with `BrandResearch` family** (per §3.1). Keep `RenderedCreative`. |
| 4. `scrape_url` tool | Not started | **New prompt + new `output_schema=BrandResearch`** (per §3.2). Test asserts BrandResearch round-trip. |
| 5. `render_creative` tool | Not started | **Update `_s3_client` + `_upload_png` for new env vars + new public URL host** (per §4). Test asserts URL ends in `.t3.tigrisfiles.io/...`. |
| 6. Register tools in MCP server | Not started | **Add `view_brand_reference` tool** (per §5.2). Server now exposes 3 tools, not 2. |
| 7. Define agents (coordinator + 2 subagents) | Not started | **Updated prompts** (per §5.3, §6.2, §6.3, §7). Coordinator gains variant_count + budget_override + status decisions. Media-buyer gains agentic objective/targeting/budget composition with $50/day cap. creative-director gains `view_brand_reference` in tools list. |
| 8. Coordinator smoke test | Not started | Updated structural assertions per §10 (objective enums, budget cap, agentic-not-baked directive, etc.). |
| 9. Chainlit app | Not started | Unchanged shape. The `BrandResearch` payload is opaque to `app.py` — it just streams whatever the SDK emits. |
| 10. Manual integration checklist | Not started | Update one bullet: confirm Tigris CDN URL renders in browser after upload. |

The new plan will be a small delta plan written next.

## 10. Testing notes

Two tests need shape changes from v1:

- `tests/test_schemas.py` — replace `AdCopy` tests with `BrandResearch` tests. Validate: required vs optional fields, exactly 3 tone adjectives (Pydantic `min_length`/`max_length`), at most 3 visual asset URLs, hex codes match `#[0-9A-Fa-f]{3,8}`.
- `tests/test_scrape.py` — mock `AsyncBrowserUse.run` to return a canned `BrandResearch`; assert the tool returns it as JSON in the text content block, and that the prompt sent to Browser Use contains "performance marketing researcher" and the URL.

One new test:

- `tests/test_view_reference.py` — assert `view_brand_reference` returns the image URL wrapped as an `image` content block with `source.type=="url"`, and rejects non-HTTPS / non-image URLs (with the `HEAD`-based `Content-Type` check stubbed for the non-extension fallback case).

`tests/test_render.py` — change two assertions only: bucket name → `ad-images-tigris`, URL host → `*.t3.tigrisfiles.io`. Body of the test stays the same.

`tests/test_agents_smoke.py` — extended structural assertions for the agentic decisions (these are prompt-text checks, not behavioral, but they catch regressions where someone re-bakes a default into the prompt):

- Coordinator prompt: mentions `variant_count`, `budget_override`, `PAUSED`, `go live`, and contains the negative directive "Do not bake adset targeting, objective, optimization goal, or budget".
- Media-buyer prompt: mentions all five objective enums (`OUTCOME_TRAFFIC`, `OUTCOME_SALES`, `OUTCOME_LEADS`, `OUTCOME_AWARENESS`, `OUTCOME_ENGAGEMENT`); mentions `$50/day` (or `5000 cents`); mentions `budget_cap_applied`; mentions `targeting_summary`.
- Creative-director prompt: tools list contains `view_brand_reference`; prompt mentions `view_brand_reference`, `primary_color_hexes`, and `creative_copy_idea.headline`.

## 11. Open items

Carrying forward from v1 §12 plus new ones:

- **(carried)** Confirm exact pipeboard tool names on first run via MCP inspector. Reconcile media-buyer prompt if they differ.
- **(carried)** Confirm whether pipeboard prefers upload-from-URL vs direct `image_url` in `create_ad_creative`. Pin the prompt to the working path.
- **(carried)** Variant render concurrency — sequential vs `asyncio.gather`. Defer until a perf complaint.
- **(carried)** Tigris retention policy — keep-forever for v1.
- **(new)** `view_brand_reference` HEAD-fallback timing. If 3-second timeout proves too slow when sites are unresponsive, reduce to 1.5s and treat timeouts as "skip this reference".
- **(new)** If a scraped `visual_asset_url` 404s by the time creative-director calls `view_brand_reference`, the tool should return a structured error (not raise) so the model gracefully proceeds without that reference. Tested as part of `test_view_reference.py`.
- **(new)** Should `view_brand_reference` enforce a per-call image size cap (e.g., reject >5 MB)? Defer; revisit if a giant image causes context bloat.
- **(new)** $50/day budget cap value is policy, not magic. If real usage tunes it, surface it as `META_DAILY_BUDGET_CEILING_CENTS` env var rather than editing the prompt each time.
- **(new)** Variant cap of 4 is a soft prompt rule, not enforced in code. If a future user phrasing pushes to 6+, decide whether to enforce in coordinator prompt or add a runtime guard.
- **(new)** Pipeboard's exact `optimization_goal` / `billing_event` enum values may differ from Meta API names. Confirm at first run; adjust the media-buyer prompt's matrix if needed.

## 12. Risks specific to v2

| Risk | Mitigation |
|---|---|
| `BrandResearch` schema is wider than `AdCopy`; Browser Use Cloud may produce more invalid outputs (more required fields → more failure surface) | Pydantic ValidationError surfaces in `scrape_url`; coordinator can retry with a sharper `extraction_goal`. Mark `visual_asset_urls` and `identity.logo_url` as the only soft fields (empty/null OK) so the common failure modes don't fail the whole call. |
| Letting media-buyer compose Meta fields agentically risks inconsistent ad copy across runs | Explicit user-chosen tradeoff for "as agentic as possible". The Meta field guidelines in the prompt (≤40 char headline, ≤125 char primary_text, ≤30 char description) constrain the variance to acceptable bands. |
| Letting media-buyer pick objective + targeting + budget agentically risks bad campaign config (wrong audience, wrong objective for the brand) | Mitigations: (a) PAUSED-by-default — nothing ships without human review in Meta Ad Manager; (b) media-buyer returns `objective`, `daily_budget_cents`, and `targeting_summary` in its result so the coordinator surfaces them in chat for the user to inspect; (c) hard $50/day budget ceiling unless the user explicitly authorized higher in the same turn. |
| User says "go live" expecting both ACTIVE *and* a higher budget; we activate but stay capped at $50/day, surprising them | Coordinator prompt makes the split explicit: "go live" alone flips status only; budget changes require an explicit dollar amount. Media-buyer's return payload includes `budget_cap_applied: true` when relevant so the coordinator can echo this back to the user. |
| Reference images from third-party sites may have hotlinking restrictions; the Anthropic API's image fetch may 403 | `view_brand_reference` returns the error as a content block; creative-director continues without that reference. |
| Tigris CDN URL (`t3.tigrisfiles.io`) propagation lag — Meta tries to fetch before CDN is warm | Meta retries; in v1 testing this hasn't been observed. Add to manual integration checklist as a known transient. |
