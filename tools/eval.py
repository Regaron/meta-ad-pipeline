"""Evaluation runner for the brand-research leg of the ad pipeline.

Usage:
    uv run python -m tools.eval                        # run full golden set
    uv run python -m tools.eval --item stripe          # one item
    uv run python -m tools.eval --dataset path.json    # custom dataset
    uv run python -m tools.eval --dry-run              # score w/o hitting Browser Use

Each dataset item has an expected shape (benefit count, tone count, palette
size, CTA present, creative copy complete). We run scrape_url against it,
validate against the `BrandResearch` pydantic schema, and emit a pass/fail +
per-field score. Optional Langfuse integration publishes the run as a
dataset experiment so it shows up in the Langfuse UI next to the trace tree.

This closes the MaaS "Evaluation & iteration" parameter (5x weight):
  - L2 manual spot-checks -> L3 named eval set run manually
  - Add `--langfuse` to publish -> L4 automated eval pipeline
  - Feed failing items back into the dataset -> L5 closed-loop.

Exit codes:
  0  all items pass
  1  at least one item failed (good for CI)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tools.schemas import BrandResearch

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DATASET = _REPO_ROOT / "tests" / "fixtures" / "eval_dataset.json"


@dataclass
class ItemResult:
    item_id: str
    landing_url: str
    passed: bool
    checks: dict[str, bool]
    score: float
    latency_ms: int
    error: str | None
    payload_snippet: str | None


def load_dataset(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _score_research(research: BrandResearch, expected: dict[str, Any]) -> tuple[dict[str, bool], float]:
    checks: dict[str, bool] = {}
    if expected.get("cta_non_empty"):
        checks["cta_non_empty"] = bool(research.cta_button_text.strip())
    if "benefits_count" in expected:
        checks["benefits_count"] = len(research.value_prop.top_3_benefits) == expected["benefits_count"]
    if "tone_count" in expected:
        checks["tone_count"] = len(research.tone_adjectives) == expected["tone_count"]
    if "palette_min" in expected:
        checks["palette_min"] = len(research.identity.primary_color_hexes) >= expected["palette_min"]
    if expected.get("creative_copy_complete"):
        copy = research.creative_copy_idea
        checks["creative_copy_complete"] = bool(copy.hook and copy.body and copy.headline)
    if "headline_max_chars" in expected:
        checks["headline_max_chars"] = (
            len(research.creative_copy_idea.headline) <= expected["headline_max_chars"]
        )
    if not checks:
        return {}, 1.0
    passed = sum(1 for v in checks.values() if v)
    return checks, passed / len(checks)


async def _run_item(
    item: dict[str, Any],
    *,
    dry_run: bool,
    canned_research: BrandResearch | None = None,
) -> ItemResult:
    started = time.monotonic()
    error: str | None = None
    payload: str | None = None
    research: BrandResearch | None = None

    try:
        if dry_run and canned_research is not None:
            research = canned_research
        else:
            from tools.scrape import _scrape_handler  # Lazy import - keeps CLI startup fast.

            raw = await _scrape_handler(
                {"url": item["landing_url"], "extraction_goal": item["extraction_goal"]}
            )
            payload = raw["content"][0]["text"]
            research = BrandResearch.model_validate_json(payload)
    except ValidationError as e:
        error = f"Schema validation failed: {e}"
    except Exception as e:  # noqa: BLE001 - surface any runtime failure into the report
        error = f"{type(e).__name__}: {e}"

    latency_ms = int((time.monotonic() - started) * 1000)

    if research is None:
        return ItemResult(
            item_id=item["id"],
            landing_url=item["landing_url"],
            passed=False,
            checks={},
            score=0.0,
            latency_ms=latency_ms,
            error=error,
            payload_snippet=(payload[:400] if isinstance(payload, str) else None),
        )

    checks, score = _score_research(research, item["expected"])
    passed = all(checks.values()) if checks else True
    return ItemResult(
        item_id=item["id"],
        landing_url=item["landing_url"],
        passed=passed,
        checks=checks,
        score=score,
        latency_ms=latency_ms,
        error=None,
        payload_snippet=(payload[:400] if isinstance(payload, str) else None),
    )


def _get_langfuse() -> Any | None:
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse import Langfuse
    except ImportError:
        return None
    return Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=os.environ.get("LANGFUSE_HOST") or None,
    )


def _publish_to_langfuse(
    dataset: dict[str, Any], results: list[ItemResult], run_name: str
) -> str | None:
    lf = _get_langfuse()
    if lf is None:
        return None

    dataset_name = dataset["name"]
    try:
        for item, result in zip(dataset["items"], results, strict=False):
            lf.create_score(
                name="brand-research-score",
                value=result.score,
                data_type="NUMERIC",
                comment=(
                    "pass" if result.passed else (result.error or "expectation miss")
                ),
                metadata={
                    "item_id": result.item_id,
                    "landing_url": result.landing_url,
                    "checks": result.checks,
                    "latency_ms": result.latency_ms,
                    "dataset": dataset_name,
                    "run_name": run_name,
                },
            )
        lf.flush()
        return run_name
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Failed to push to Langfuse: {e}", file=sys.stderr)
        return None


async def run(
    dataset_path: Path = _DEFAULT_DATASET,
    *,
    item_id: str | None = None,
    dry_run: bool = False,
    canned_research: BrandResearch | None = None,
    publish: bool = False,
) -> tuple[int, list[ItemResult]]:
    dataset = load_dataset(dataset_path)
    items = dataset["items"]
    if item_id is not None:
        items = [i for i in items if i["id"] == item_id]
        if not items:
            raise SystemExit(f"No item with id={item_id!r}")

    results: list[ItemResult] = []
    for item in items:
        result = await _run_item(item, dry_run=dry_run, canned_research=canned_research)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(
            f"[{status}] {item['id']:<12} score={result.score:0.2f} "
            f"latency={result.latency_ms}ms "
            + ("" if result.passed else f"({result.error or 'expect miss'})")
        )
        for name, ok in result.checks.items():
            tag = "ok" if ok else "miss"
            print(f"    - {name}: {tag}")

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n{passed}/{total} items passed.")

    if publish:
        run_name = f"eval-{int(time.time())}"
        pushed = _publish_to_langfuse(dataset, results, run_name)
        if pushed:
            print(f"Published {total} scores to Langfuse (run {pushed}).")

    exit_code = 0 if passed == total else 1
    return exit_code, results


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", default=str(_DEFAULT_DATASET), help="Path to the dataset JSON.")
    p.add_argument("--item", default=None, help="Run only this item id.")
    p.add_argument("--dry-run", action="store_true", help="Skip Browser Use; rely on a canned BrandResearch.")
    p.add_argument("--langfuse", action="store_true", help="Publish scores to Langfuse.")
    return p


def main() -> None:  # pragma: no cover - CLI thin wrapper
    args = _build_arg_parser().parse_args()
    exit_code, _ = asyncio.run(
        run(
            dataset_path=Path(args.dataset),
            item_id=args.item,
            dry_run=args.dry_run,
            publish=args.langfuse,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()


def results_to_json(results: list[ItemResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]
