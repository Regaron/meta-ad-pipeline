#!/usr/bin/env -S uv run python
"""Drive one full pipeline turn headlessly.

Bypasses Chainlit so you can execute an end-to-end run from CI or a
terminal: scrape -> creative-director renders -> media-buyer publishes.

Emits exactly the same Langfuse trace as the Chainlit app does (the tracer
is the same `TraceSession`), so this also works as the "activate one
real output" evidence step for MaaS Real-Output L4.

By default, status=PAUSED. Pass --activate to flip to ACTIVE; the media
buyer's cap-applied safety still prevents budget overruns. Requires
`TEST_ALLOW_ACTIVATION=1` in the environment before --activate is honoured
(double guard against accidental live spend).

Usage:
    uv run python scripts/run_pipeline_headless.py \\
        --url https://acme.example.com \\
        --variants 2
    uv run python scripts/run_pipeline_headless.py \\
        --url https://acme.example.com --variants 1 --activate
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv

load_dotenv()


def _build_prompt(url: str, variants: int, activate: bool, budget: int | None) -> str:
    pieces = [f"Build a Meta ad for {url}."]
    if variants == 1:
        pieces.append("Just one creative.")
    elif variants >= 3:
        pieces.append(f"Test a bunch of creatives ({variants} variants).")
    if budget is not None:
        pieces.append(f"Use a ${budget}/day budget.")
    if activate:
        pieces.append("Go live.")
    return " ".join(pieces)


async def run_once(url: str, variants: int, activate: bool, budget: int | None) -> int:
    if activate and os.environ.get("TEST_ALLOW_ACTIVATION") != "1":
        print(
            "Refusing to activate: set TEST_ALLOW_ACTIVATION=1 to confirm.",
            file=sys.stderr,
        )
        return 2

    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        query,
    )

    from app import build_options
    from tools.tracing import TraceSession

    prompt = _build_prompt(url, variants, activate, budget)
    print(f"Prompt: {prompt}\n")

    session_id = f"headless-{uuid.uuid4().hex[:8]}"

    async with TraceSession(user_session_id=session_id, prompt=prompt) as trace:
        last_result: ResultMessage | None = None
        async for event in query(prompt=prompt, options=build_options(None)):
            await trace.ingest(event)
            if isinstance(event, ResultMessage):
                last_result = event
                continue
            if isinstance(event, AssistantMessage) and not event.parent_tool_use_id:
                for block in event.content:
                    if isinstance(block, TextBlock):
                        sys.stdout.write(block.text)
                        sys.stdout.flush()

    print()
    if last_result is not None:
        print("---")
        print(f"num_turns={last_result.num_turns}  "
              f"duration_ms={last_result.duration_ms}  "
              f"cost_usd={last_result.total_cost_usd}")
        if last_result.is_error:
            print(f"is_error=True stop_reason={last_result.stop_reason}")
            return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True)
    p.add_argument("--variants", type=int, default=2)
    p.add_argument("--activate", action="store_true")
    p.add_argument("--budget", type=int, default=None, help="Daily budget USD.")
    args = p.parse_args()
    return asyncio.run(run_once(args.url, args.variants, args.activate, args.budget))


if __name__ == "__main__":
    sys.exit(main())
