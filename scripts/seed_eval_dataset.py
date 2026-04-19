#!/usr/bin/env -S uv run python
"""Push tests/fixtures/eval_dataset.json to Langfuse as a dataset.

Idempotent: creates the dataset if missing and adds any items whose `id` is
not already present. Existing items are left alone (so you can hand-edit
expected outputs in the Langfuse UI without them being clobbered).

Usage:
    uv run python scripts/seed_eval_dataset.py
    uv run python scripts/seed_eval_dataset.py --dataset path.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DATASET = _REPO_ROOT / "tests" / "fixtures" / "eval_dataset.json"


def _require_keys() -> None:
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        if not os.environ.get(k):
            print(f"ERROR: Missing {k}. Set it in .env.", file=sys.stderr)
            raise SystemExit(2)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", default=str(_DEFAULT_DATASET))
    args = p.parse_args()

    _require_keys()
    from langfuse import Langfuse

    ds_path = Path(args.dataset)
    with ds_path.open(encoding="utf-8") as f:
        ds = json.load(f)
    name = ds["name"]
    description = ds.get("description", "")

    lf = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        host=os.environ.get("LANGFUSE_HOST") or None,
    )

    # Ensure the dataset exists. The SDK exposes create via the API shim -
    # tolerate an "already exists" error.
    try:
        lf.api.datasets.create(
            request={"name": name, "description": description}
        )
        print(f"Created dataset {name!r}")
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "exists" in msg or "duplicate" in msg or "409" in msg:
            print(f"Dataset {name!r} already exists; reusing.")
        else:
            print(f"WARN: create dataset failed: {e}", file=sys.stderr)

    # Fetch existing items so we can upsert by external id.
    existing_ids: set[str] = set()
    try:
        items = lf.api.dataset_items.list(dataset_name=name, limit=500)
        for item in getattr(items, "data", []) or []:
            meta = getattr(item, "metadata", None) or {}
            ext_id = meta.get("item_id") if isinstance(meta, dict) else None
            if ext_id:
                existing_ids.add(ext_id)
    except Exception as e:  # noqa: BLE001
        print(f"WARN: could not list existing items: {e}", file=sys.stderr)

    added = 0
    skipped = 0
    for item in ds["items"]:
        if item["id"] in existing_ids:
            skipped += 1
            continue
        lf.create_dataset_item(
            dataset_name=name,
            input={
                "landing_url": item["landing_url"],
                "extraction_goal": item["extraction_goal"],
            },
            expected_output=item["expected"],
            metadata={
                "item_id": item["id"],
                "tags": item.get("tags", []),
            },
        )
        added += 1

    lf.flush()
    print(f"Dataset {name!r}: {added} added, {skipped} skipped (already present).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
