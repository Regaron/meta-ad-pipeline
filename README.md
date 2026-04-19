# Ad Pipeline

Conversational Meta ad pipeline. Paste a landing URL and the coordinator
orchestrates a creative director and a media buyer end-to-end:

1. **Scrape** the landing page (Browser Use Cloud) into structured
   `BrandResearch` — headline, body, hook, benefits, palette, CTA, tone.
2. **Design** HTML ad creatives (1080×1080) following strict hierarchy /
   typography / palette rules, then rasterize to PNG and upload to Tigris.
3. **Publish** campaigns, ad sets, creatives, and ads to Meta via the
   pipeboard MCP. Accounts/pages are auto-discovered — you never have to
   type an `act_` or page ID.

Every turn emits a full Langfuse trace (agent / tool / generation spans with
per-step token + cost) plus a mirrored Chainlit step tree so the UI shows
what's happening live.

## Quickstart

```bash
# 1. Install (requires Python 3.11+ and uv)
uv sync

# 2. Configure secrets
cp .env.example .env
# Fill in:
#   - BROWSER_USE_API_KEY         (https://cloud.browser-use.com)
#   - PIPEBOARD_OAUTH_TOKEN       (pipeboard Meta Ads MCP OAuth)
#   - TIGRIS_STORAGE_*            (https://console.tigris.dev)
#   - LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST (optional — leave blank to disable tracing)

# 3. Run the Chainlit app
uv run chainlit run app.py
```

Then try prompts like:

- `Build a Meta ad for https://acme.example.com`
- `Creative bake-off for https://acme.example.com, $25/day, go live`
- `How is campaign abc123 doing so far?`

## Architecture

```
┌──────────────┐    ┌────────────────────┐   ┌────────────────────┐
│ User (Chainlit) ─▶ Coordinator (Claude) ─▶ creative-director
└──────────────┘    │  tools:            │   │  tools:            │
                    │   - scrape_url     │   │   - render_creative│
                    │   - Agent (delegate)   │   - view_brand_ref │
                    └──────────┬─────────┘   └────────────────────┘
                               ▼
                         media-buyer
                         tools: pipeboard MCP
                          - list_ad_accounts (auto-discovery)
                          - list_pages (auto-discovery)
                          - create_campaign / adset / creative / ad
                          - insights / list_campaigns
```

- `agents.py` — system prompts for coordinator, creative-director,
  media-buyer.
- `app.py` — Chainlit entrypoint + permission callback that blanket-allows
  every MCP tool (Chainlit has no interactive approval UI).
- `tools/mcp_server.py` — local MCP server exposing `scrape_url`,
  `render_creative`, `view_brand_reference`.
- `tools/tracing.py` — event-stream ingestor that emits Langfuse spans and
  mirrored Chainlit steps from the same stream, plus styled in-chat cards
  when `scrape_url` / `render_creative` return.
- `tools/creative.py` — HTML → PDF → PNG pipeline via WeasyPrint +
  pypdfium2, uploaded to Tigris via boto3.

## Observability

See [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md) for the trace shape,
filtering recipes, and a 60-second mentor walkthrough. Headline:

- One Langfuse trace per Chainlit turn, `session_id` grouping every turn
  in a conversation.
- Subagent spans (`creative-director`, `media-buyer`) nest under the
  coordinator so the tree mirrors the runtime hierarchy.
- Each assistant turn is a `generation` observation with `model` + Anthropic
  usage fields mapped to Langfuse's `input` / `output` / cache token keys,
  so per-step cost auto-computes in the Langfuse UI.
- Tool errors surface as `level=ERROR` spans; the root span carries
  `total_cost_usd`, `num_turns`, `duration_ms`, `permission_denials`.

## Tests

```bash
uv run pytest tests/                              # unit + smoke
uv run pytest tests/ --ignore=tests/integration   # unit only (default CI path)
uv run pytest tests/integration -m integration_live  # live suite (needs real keys)
```

The live suite boots Chainlit, drives a real scrape → render → publish
flow, and asserts the trace tree. It requires `BROWSER_USE_API_KEY`,
`PIPEBOARD_OAUTH_TOKEN`, Tigris credentials, and the `TEST_META_*` env
vars.
