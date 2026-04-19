# Evaluation & iteration

Golden-set eval for the scrape → creative pipeline. Closes the MaaS
**Evaluation & iteration** rubric parameter (5× weight).

## What it scores

Every item in `tests/fixtures/eval_dataset.json` declares a landing URL, an
extraction goal, and an `expected` block with the shape the resulting
`BrandResearch` must satisfy:

| Check | Meaning |
| --- | --- |
| `cta_non_empty` | `cta_button_text` must be a non-blank string. |
| `benefits_count` | `value_prop.top_3_benefits` length == expected (default 3). |
| `tone_count` | `tone_adjectives` length == expected (default 3). |
| `palette_min` | `identity.primary_color_hexes` length ≥ expected. |
| `creative_copy_complete` | `hook`, `body`, and `headline` all non-empty. |
| `headline_max_chars` | `creative_copy_idea.headline` length ≤ expected. |

A run is a pass iff every check on every item passes. The scorer returns a
per-item score between 0 and 1 so you can track drift even when everything
"passes".

## Run locally

```bash
# Full golden set against live Browser Use (spends credits):
uv run python -m tools.eval

# One item, no network:
uv run python -m tools.eval --item stripe --dry-run    # short-circuits with a canned fixture

# Publish per-item scores to Langfuse so the dashboard shows the run alongside traces:
uv run python -m tools.eval --langfuse
```

Exit codes: `0` all pass, `1` at least one failure. Good for CI.

## Seeding the dataset to Langfuse

```bash
uv run python scripts/seed_eval_dataset.py
```

Idempotent: the script creates the dataset if missing and adds only new
items by `id`. Hand-edit expectations in the Langfuse UI without them being
clobbered.

## Rubric level progression

- **L2 (5pt)** manual spot-checks — what existed before this file.
- **L3 (10pt)** named eval set, run manually — `uv run python -m tools.eval`.
- **L4 (15pt)** automated eval pipeline — add this command to CI; require
  exit 0 to merge; `--langfuse` publishes per-item `brand-research-score`
  scores so the dashboard tracks regression over time.
- **L5 (20pt)** closed-loop — when a new failure mode is spotted in
  production (e.g. an L5 trace with `level=ERROR`), copy its landing URL +
  extraction goal into `eval_dataset.json`, lock in the expected shape,
  re-run, and commit. The eval set grows from user-observed failures.

## Extending the expected shape

New check names go in two places:
1. `tools/eval.py::_score_research` — add the boolean in `checks`.
2. `eval_dataset.json` items — add the key under `expected`.

Keep checks cheap to evaluate; the eval loop is meant to run in CI.
