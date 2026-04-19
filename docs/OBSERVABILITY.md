# Observability — Langfuse traces for the ad pipeline

Every Chainlit turn opens one Langfuse trace. The trace mirrors the runtime
hierarchy so a mentor can click into any step and see what happened.

## Trace structure

```
ad-pipeline.turn            (agent — the coordinator's lifecycle for one user message)
├─ coordinator.turn         (generation — one LLM turn, model = claude-opus-4-7)
├─ Scrape landing page      (tool — mcp__adpipeline__scrape_url)
├─ subagent.creative-director   (agent — wrapper for the delegated turn)
│  ├─ creative-director.turn    (generation — one LLM turn)
│  ├─ View brand reference      (tool — mcp__adpipeline__view_brand_reference)
│  ├─ Render creative PNG       (tool — mcp__adpipeline__render_creative)
│  └─ …
└─ subagent.media-buyer         (agent)
   ├─ media-buyer.turn          (generation)
   ├─ Meta Ads: list ad accounts   (tool — mcp__pipeboard__…)
   ├─ Meta Ads: create campaign    (tool)
   └─ …
```

- **Root `ad-pipeline.turn`** — opens with the user prompt as `input`, closes
  on the SDK's `ResultMessage` with `total_cost_usd`, `num_turns`,
  `duration_ms`, `model_usage` on metadata.
- **Per-turn `generation`** — captures `model`, `input_tokens`,
  `output_tokens`, and Anthropic's cache-hit tokens. Langfuse auto-prices
  from `model`, so per-step cost is visible in the UI without extra work.
- **Subagent `agent` spans** — opened when the coordinator invokes the
  `Agent` tool with `subagent_type` ∈ {`creative-director`, `media-buyer`}.
  All child generations and tool spans nest inside the wrapper so you can
  filter the trace to a single subagent's work.
- **Tool `tool` spans** — keyed by `tool_use_id`; they close when the
  matching `ToolResultBlock` arrives. Errors set `level=ERROR` and
  `status_message=tool_error`.

## Trace metadata you can filter on

| Field | Where to find it |
| --- | --- |
| `session_id` | Trace header — set from the Chainlit `user_session.id`, groups every turn of a conversation. |
| `agent` (= coordinator / creative-director / media-buyer) | Generation + agent-span metadata. |
| `tool_name` | Tool-span metadata (raw `mcp__…` name). |
| `stop_reason`, `message_id` | Generation metadata. |
| `total_cost_usd`, `num_turns`, `duration_ms` | Root-span metadata on `ResultMessage`. |
| `permission_denials`, `errors`, `subtype` | Root-span metadata. |

## Setup

1. Create a Langfuse project (https://cloud.langfuse.com or
   https://us.cloud.langfuse.com).
2. Copy Public / Secret keys into `.env`:
   ```
   LANGFUSE_PUBLIC_KEY=pk-lf-…
   LANGFUSE_SECRET_KEY=sk-lf-…
   LANGFUSE_HOST=https://cloud.langfuse.com
   ```
3. Leave the keys blank to disable tracing entirely; the app falls back
   to Chainlit-step-only visibility and never blocks.

## Levels hit (per GrowthX MaaS rubric, observability 7× weight)

| Rubric requirement | How it's satisfied |
| --- | --- |
| L3 — pull up a specific run step-by-step | Every run has its own trace page with nested span tree. |
| L4 — trace tree across agents, token + cost per step, filter by agent/task | Agent-type spans + `as_type=generation` with `model` + usage enable Langfuse's auto cost per step; filters: **observation type = generation**, **metadata.agent = creative-director**, **metadata.tool_name = mcp__pipeboard__create_campaign**. |
| L5 — diff two runs, failure alerts, cost spike alerts, search across runs | Langfuse UI: **Traces → select two → Compare**; **Settings → Alerts → new alert on `level = ERROR` OR `total_cost_usd > $0.50`**; `session_id` groups every turn of a conversation so search/facet queries work across runs. |

## Verify cost pricing before the demo

Langfuse auto-prices a generation only when the project's model catalog
contains an exact `model` match. Run the verification script once after
configuring keys to confirm:

```bash
uv run python scripts/verify_langfuse.py
```

- **PASS** — a Claude generation with our usage keys resolves to a
  non-zero cost. Observability L4 is unlocked.
- **FAIL** — the model id isn't in the Langfuse catalog. In Langfuse UI go
  to **Settings → Models → Add model**, paste the exact id from the script
  output (e.g. `claude-opus-4-7`), configure input/output per-1k-token
  prices, re-run.

## Dashboard walk-through (60-second mentor demo)

1. **Traces tab** — pick the most recent trace. Expand the tree:
   `ad-pipeline.turn → subagent.creative-director → Render creative`.
   Click any generation — `input`, `output`, model, tokens, and cost land
   on the right-hand panel (**L3 requirement**).
2. **Filter chip: `observation type = generation` + `metadata.agent =
   creative-director`** — isolates the subagent's LLM turns with
   per-step cost (**L4 requirement — filter by agent/task**).
3. **Select two traces → Compare** — side-by-side diff of the
   input/output payloads, per-step cost, and run duration (**L5
   requirement — diff two runs**).
4. **Sessions tab** — every Chainlit conversation (`session_id` set from
   the Chainlit session UUID) rolls up here with aggregate tokens, cost,
   and number of traces.
5. **Alerts tab** (L5) — show the two pre-configured alerts described
   below.

## L5 alerts to configure once (required for L5 scoring)

In Langfuse: **Settings → Alerts → New alert**.

1. **Trace-level failure alert**
   - Filter: `level = ERROR`
   - Scope: traces
   - Channel: Slack / email / webhook
2. **Cost-spike alert**
   - Filter: `total_cost_usd > 0.50` (tune to your baseline)
   - Scope: traces
   - Channel: same as above
3. **Per-step failure alert** (optional — catches tool errors before the
   whole trace rolls up)
   - Filter: `level = ERROR` on observations with `type in [tool, agent]`
   - Scope: observations

Capture screenshots of the alert list into the submission's
`/evidence` folder so the rubric's L5 alerts check is defensible without
a live failure at demo time.

## Searching runs (L5 requirement: search across runs)

Langfuse's UI search bar supports:

- `session_id = …` — grabs every turn of a given Chainlit conversation.
- `tag = ad-pipeline` — the TraceSession tags every trace with
  `ad-pipeline` and `chainlit`.
- `metadata.tool_name = mcp__pipeboard__create_campaign` — jump straight
  to every campaign creation.
- `metadata.agent = media-buyer` — isolate media-buyer behaviour.

## Troubleshooting

- **Spans missing** → Langfuse keys unset; the app logs nothing but silently
  disables tracing. Check `.env`.
- **No token counts on a generation** → the SDK returned `usage = None`
  (happens for some cached turns). The generation still carries the model,
  so Langfuse can still price empty usage at zero.
- **Costs look off** → set the exact model id (`claude-opus-4-7`) in
  Langfuse's model catalog; the SDK already passes it verbatim.
