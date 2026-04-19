# Design v3: Conversational Meta Ad Pipeline - automated live integration

- **Date:** 2026-04-19
- **Status:** Draft (approved design update; ready for plan rewrite after user review)
- **Layered on:** [`2026-04-19-meta-ad-pipeline-design-v2.md`](./2026-04-19-meta-ad-pipeline-design-v2.md)
- **Implementation status:** worktree `meta-ad-pipeline` now has v2 tasks 1-9 substantially implemented. v3 does not replace that work. It amends `app.py` to fix the open Task 9 review issues and replaces the v2 manual integration checklist with an automated live pytest integration suite.
- **Terminal state of brainstorming:** this spec. Next step: writing-plans.

## 1. Why a v3

Two things changed after v2:

1. The remaining manual integration checklist should become **automatic**.
2. Task 9 review surfaced two real UI/runtime issues in `app.py`:
   - subagent `cl.Step` lifecycle is not closed when one subagent stream ends and another begins in the same turn,
   - creative image previews are deduplicated only within one text block and are tied too tightly to one hardcoded host pattern.

The design goal in v3 is to automate the real sandbox validation path without giving up the safety guarantees from v1/v2:

- real Browser Use scrape,
- real creative rendering and Tigris upload,
- real pipeboard publish into a sandbox Meta account,
- **PAUSED by default**,
- optional activation only behind an explicit opt-in flag.

Everything else from v2 stays: single-process Chainlit app, top-level coordinator plus two subagents, in-process MCP server, Tigris CDN public URLs on `t3.tigrisfiles.io`, and agentic publish decisions by the media-buyer.

## 2. Delta Summary

| Area | v2 | v3 |
|---|---|---|
| Task 9 app runtime | App boots and streams, but current review found open-step lifecycle issues and per-block image dedupe only | `app.py` is amended to close prior subagent steps when the parent tool changes, and to dedupe image previews across the full streamed turn |
| Integration verification | `docs/superpowers/integration-checklist-v2.md` manual checklist | Replaced with an automated live pytest suite under `tests/integration/` |
| Chainlit smoke | Manual boot step in checklist/final verification | Automated as a pytest subprocess smoke |
| Live publish verification | Manual browser/operator workflow | Automated paused sandbox publish test using real services |
| Analytics follow-up | Manual | Automated resumed-session test using the real created campaign |
| Activation | Manual "go live" checklist step | Optional automated live activation test behind `TEST_ALLOW_ACTIVATION=1` |

## 3. App.py Amendments

### 3.1 Public options builder

`app.py` should expose a **public** helper:

```python
def build_options(resume_session_id: str | None) -> ClaudeAgentOptions:
    ...
```

This replaces the private `_build_options` name from v2. The Chainlit runtime and the live integration tests both import and use this same builder so there is exactly one source of truth for:

- coordinator prompt,
- `allowed_tools`,
- `agents=build_agents()`,
- in-process `adpipeline` SDK MCP server,
- `pipeboard` HTTP MCP config and bearer auth,
- model and resume wiring.

This is a narrow API promotion, not a new module.

### 3.2 Step lifecycle fix

`on_message` should track both:

- `current_subagent_step: cl.Step | None`
- `current_parent_tool_use_id: str | None`

Behavior:

1. When an `AssistantMessage` arrives with no `parent_tool_use_id`, stream it to the main assistant message.
2. When an `AssistantMessage` arrives with a `parent_tool_use_id`:
   - if there is **no** current step, open one,
   - if the parent id matches the current step's parent id, keep streaming into the same step,
   - if the parent id is **different**, close the current step first, then open a new step for the new parent id.
3. At the end of the query loop, close any still-open step.

This fixes the current ambiguity where a single user turn can produce multiple subagent/tool streams but only the last step is ever explicitly closed.

### 3.3 Image preview dedupe across the full turn

`app.py` should maintain:

```python
seen_image_urls: set[str] = set()
```

for the lifetime of one `on_message` call.

When a text block is processed:

1. extract candidate creative URLs,
2. for each URL not yet seen in this turn, emit exactly one inline `cl.Image` message,
3. add it to `seen_image_urls`.

This prevents duplicate image previews when the same URL appears in multiple streamed chunks or repeated assistant blocks.

### 3.4 Public-host-aware Tigris URL extraction

The regex helper should no longer hardcode `t3.storage.dev`.

Instead:

- read `TIGRIS_PUBLIC_HOST` from env, defaulting to `t3.tigrisfiles.io`,
- match URLs shaped like:

```text
https://<bucket>.<public-host>/creatives/<variant>.png
```

Implementation detail: escape the configured host in the regex. The helper may remain private (`_extract_tigris_urls`).

## 4. Automated Live Integration Suite

The manual checklist is replaced by a live pytest suite under:

```text
tests/
  integration/
    conftest.py
    test_chainlit_boot.py
    test_live_pipeline.py
```

### 4.1 Invocation model

These are **not** unit tests. They are explicit live integration tests.

Recommended invocation:

```bash
uv run pytest tests/integration -m integration_live -v
```

Default/local suite invocation remains unit-focused and should exclude these tests unless explicitly requested.

`pyproject.toml` should register a pytest marker:

```toml
[tool.pytest.ini_options]
markers = [
    "integration_live: talks to live external services (Anthropic/Browser Use/Tigris/Pipeboard/Meta sandbox)",
]
```

### 4.2 Required environment

The live suite requires:

- `BROWSER_USE_API_KEY`
- `PIPEBOARD_OAUTH_TOKEN`
- `TIGRIS_STORAGE_ACCESS_KEY_ID`
- `TIGRIS_STORAGE_SECRET_ACCESS_KEY`
- `TIGRIS_STORAGE_BUCKET`
- optional `TIGRIS_API_ENDPOINT` (default `https://t3.storage.dev`)
- optional `TIGRIS_PUBLIC_HOST` (default `t3.tigrisfiles.io`)
- `TEST_LANDING_URL`
- `TEST_META_AD_ACCOUNT_ID`
- `TEST_META_PAGE_ID`
- optional `TEST_ALLOW_ACTIVATION=1`
- one of:
  - `ANTHROPIC_API_KEY`, or
  - valid local Claude CLI login

If the live suite is invoked without the required env, it should fail fast with a clear aggregated error listing the missing variables. It should not silently skip after the user explicitly asked for full live integration.

### 4.3 Shared fixtures

`tests/integration/conftest.py` should provide:

1. `live_settings` fixture:
   - validates required env vars,
   - returns a typed settings object/dict for tests.
2. `test_run_label` fixture:
   - generates a short unique label, e.g. `codex-it-<timestamp>`.
3. `coordinator_options` fixture:
   - imports `app.build_options(None)` so tests use the exact same runtime options as the app.
4. `live_build_result` fixture (module scope):
   - runs the real coordinator flow once,
   - yields structured artifacts needed by later assertions:
     - SDK `session_id`
     - parsed creative payload
     - parsed media-buyer publish payload
     - returned campaign/adset/creative/ad ids
     - any surfaced targeting/budget/objective summary.

The module-scoped fixture is deliberate: it prevents duplicate real ad creation when multiple assertions need the same campaign.

## 5. Live Test Cases

### 5.1 `test_chainlit_boot.py`

Purpose: replace the manual app-boot checklist step.

Behavior:

- start `uv run chainlit run app.py --headless --port <free-port>` in a subprocess,
- wait up to ~10 seconds for the expected availability log,
- assert no import/runtime error appears before shutdown,
- terminate the subprocess cleanly.

This is still local, but it lives alongside the integration suite because it validates the real entrypoint used in the live tests.

### 5.2 `test_live_paused_campaign_creation`

Purpose: replace the bulk of the manual checklist with one automated live run.

Call the real coordinator via `claude_agent_sdk.query()` using `app.build_options(None)` and a prompt shaped like:

> "Build a paused sandbox integration test ad for `<TEST_LANDING_URL>`, ad account `<TEST_META_AD_ACCOUNT_ID>`, page `<TEST_META_PAGE_ID>`, 2 variants, label `<test_run_label>`."

Assertions:

- the stream yields a real `ResultMessage` with `session_id`,
- at least one subagent event occurs for `creative-director`,
- creative-director final output parses as a JSON array with at least one `{variant_id, variant_note, png_url}`,
- every `png_url` is on `https://<bucket>.<TIGRIS_PUBLIC_HOST>/creatives/...png`,
- at least one subagent event occurs for `media-buyer`,
- media-buyer final output parses as JSON containing:
  - `campaign_id`
  - `adset_id`
  - `creative_ids`
  - `ad_ids`
- returned budget is within the v2 capped range (`500` to `5000` cents) unless an explicit budget override is part of the prompt,
- publish result indicates paused/default-safe behavior,
- the final assistant text is non-empty.

This test is the automated replacement for:

- scrape path,
- creative path,
- paused publish path,
- objective/targeting/budget composition checks.

### 5.3 `test_live_campaign_analytics_followup`

Purpose: replace the manual analytics follow-up check.

Using the `session_id` and `campaign_id` from `live_build_result`, call `query()` again with `resume=<session_id>` and a prompt like:

> "How is campaign `<campaign_id>` doing?"

Assertions:

- the response completes successfully,
- the assistant returns non-empty analytics text,
- the answer is not just a restatement of the prompt,
- the response is reasonably concise (for example, not a raw tool dump),
- the stream contains evidence that the resumed conversation path worked (same session resumed, no fresh build needed).

### 5.4 `test_live_campaign_activation_override` (optional)

Guarded by:

```text
TEST_ALLOW_ACTIVATION=1
```

Prompt shape:

> "Go live on campaign `<campaign_id>`."

Assertions:

- media-buyer performs the activation path,
- returned status is `ACTIVE`,
- daily budget is unchanged from the paused build unless the activation prompt itself explicitly included a higher dollar amount.

This test is opt-in because it changes live campaign state.

## 6. What is intentionally not automated

The live suite does **not** try to automate Meta UI browser verification.

The authoritative automated checks are:

- successful real service calls,
- returned structured IDs from pipeboard,
- Tigris public URLs under the configured CDN host,
- resumed-session analytics behavior,
- paused-by-default safety,
- optional activation path.

If a human wants to inspect the resulting campaign in Meta Ad Manager, that remains an operator follow-up, not a required test step.

## 7. Plan Impact

v2 Task 10 is replaced entirely.

Old Task 10:

- create `docs/superpowers/integration-checklist-v2.md`
- walk it manually

New Task 10:

- amend `app.py` with the step-lifecycle and cross-turn image-dedupe fixes,
- add `tests/integration/conftest.py`,
- add `tests/integration/test_chainlit_boot.py`,
- add `tests/integration/test_live_pipeline.py`,
- register the `integration_live` marker in `pyproject.toml`,
- verify the live suite with the required env present.

v2 Task 11 changes accordingly:

- keep the full unit suite,
- keep MCP server registration verification,
- keep app import verification,
- replace "walk through the manual checklist" with explicit execution of the live integration suite.

## 8. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Live integration tests can spend money or create noisy sandbox artifacts | Always publish PAUSED by default; keep activation behind `TEST_ALLOW_ACTIVATION=1`; require sandbox ad account/page ids |
| Live tests may be flaky due external APIs | Keep assertions on stable artifacts (IDs, URLs, non-empty responses) rather than brittle prose matching; use one module-scoped build fixture to reduce repeated calls |
| Drift between app runtime wiring and test wiring | Tests import and use `app.build_options()` directly rather than recreating `ClaudeAgentOptions` by hand |
| Duplicate creative previews in UI | Track `seen_image_urls` across the full turn |
| Multiple subagent streams can leave stale step state | Close the current step whenever `parent_tool_use_id` changes before opening the next |
| CDN host changes would break preview regex silently | Read `TIGRIS_PUBLIC_HOST` from env and compile the regex from that value |

## 9. Open Items

- Decide whether the live suite should serialize itself with a project-local lock if multiple operators may run it at once against the same sandbox account. Default assumption: one operator at a time.
- If pipeboard's returned publish payload shape differs materially from the v2 prompt assumptions, the tests should parse conservatively and assert only on the stable ids and safety fields.
- If Browser Use or Anthropic latency is too high for a single pytest timeout, add generous per-test timeouts rather than splitting the end-to-end paused-build case into smaller fake tests.
