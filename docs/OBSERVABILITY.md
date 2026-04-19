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

## Dashboard walk-through (for the 60-second mentor demo)

1. **Traces tab** — pick a recent trace. The tree shows
   `ad-pipeline.turn → subagent.creative-director → Render creative PNG`.
   Click any generation: `input`, `output`, model, tokens, and cost are on
   the right-hand panel.
2. **Select two traces → Compare** — side-by-side diff of the input/output
   payloads and aggregate cost. Use this to show regressions between runs.
3. **Sessions tab** — every Chainlit conversation rolls up here; token and
   cost totals are aggregated across turns.
4. **Settings → Alerts** — wire up `level = ERROR` to Slack; wire up a
   `cost > threshold` alert for spike detection. Screenshots in the
   `/evidence` folder of the submission.

## Troubleshooting

- **Spans missing** → Langfuse keys unset; the app logs nothing but silently
  disables tracing. Check `.env`.
- **No token counts on a generation** → the SDK returned `usage = None`
  (happens for some cached turns). The generation still carries the model,
  so Langfuse can still price empty usage at zero.
- **Costs look off** → set the exact model id (`claude-opus-4-7`) in
  Langfuse's model catalog; the SDK already passes it verbatim.
