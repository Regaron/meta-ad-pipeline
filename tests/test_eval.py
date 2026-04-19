"""Tests for tools.eval."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tools.eval import _DEFAULT_DATASET, _score_research, load_dataset, run
from tools.schemas import BrandResearch

_CANNED = BrandResearch(
    source_url="https://acme.com",
    identity={
        "logo_url": "https://acme.com/logo.svg",
        "primary_color_hexes": ["#0F62FE", "#161616"],
    },
    value_prop={
        "headline": "Premium widgets engineered to last.",
        "top_3_benefits": ["Free shipping", "Lifetime warranty", "30-day returns"],
    },
    visual_asset_urls=[],
    tone_adjectives=["confident", "warm", "playful"],
    cta_button_text="Get Started",
    creative_copy_idea={
        "hook": "Tired of widgets that break?",
        "body": "Built from aerospace-grade materials.",
        "headline": "Widgets That Outlast You",
    },
)


def test_default_dataset_is_valid_json_and_has_items():
    ds = load_dataset(_DEFAULT_DATASET)
    assert isinstance(ds["items"], list) and len(ds["items"]) >= 5
    for item in ds["items"]:
        for k in ("id", "landing_url", "extraction_goal", "expected"):
            assert k in item, f"Missing {k} in {item}"


def test_score_research_passes_when_expectations_met():
    expected = {
        "cta_non_empty": True,
        "benefits_count": 3,
        "tone_count": 3,
        "palette_min": 1,
        "creative_copy_complete": True,
        "headline_max_chars": 80,
    }
    checks, score = _score_research(_CANNED, expected)
    assert all(checks.values()), checks
    assert score == 1.0


def test_score_research_flags_missing_cta():
    bad = _CANNED.model_copy(deep=True, update={"cta_button_text": " "})
    checks, score = _score_research(bad, {"cta_non_empty": True})
    assert checks["cta_non_empty"] is False
    assert score == 0.0


def test_score_research_flags_headline_too_long():
    too_long = _CANNED.model_copy(deep=True)
    too_long.creative_copy_idea.headline = "x" * 200
    checks, _ = _score_research(too_long, {"headline_max_chars": 80})
    assert checks["headline_max_chars"] is False


@pytest.mark.asyncio
async def test_run_dry_run_uses_canned_research(tmp_path):
    dataset = {
        "name": "tiny",
        "items": [
            {
                "id": "one",
                "landing_url": "https://x.com",
                "extraction_goal": "g",
                "expected": {
                    "cta_non_empty": True,
                    "benefits_count": 3,
                    "tone_count": 3,
                    "palette_min": 1,
                    "creative_copy_complete": True,
                    "headline_max_chars": 80,
                },
            }
        ],
    }
    path = tmp_path / "ds.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")

    exit_code, results = await run(
        dataset_path=path, dry_run=True, canned_research=_CANNED
    )
    assert exit_code == 0
    assert len(results) == 1
    assert results[0].passed is True
    assert results[0].score == 1.0


@pytest.mark.asyncio
async def test_run_live_path_invokes_scrape_handler(tmp_path):
    dataset = {
        "name": "tiny",
        "items": [
            {
                "id": "one",
                "landing_url": "https://x.com",
                "extraction_goal": "g",
                "expected": {
                    "cta_non_empty": True,
                    "benefits_count": 3,
                    "tone_count": 3,
                    "palette_min": 1,
                    "creative_copy_complete": True,
                    "headline_max_chars": 80,
                },
            }
        ],
    }
    path = tmp_path / "ds.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")

    mock_handler = AsyncMock(
        return_value={"content": [{"type": "text", "text": _CANNED.model_dump_json()}]}
    )
    with patch("tools.scrape._scrape_handler", mock_handler):
        exit_code, results = await run(dataset_path=path, dry_run=False)

    assert exit_code == 0
    assert results[0].score == 1.0
    mock_handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_exits_nonzero_on_expectation_miss(tmp_path):
    dataset = {
        "name": "tiny",
        "items": [
            {
                "id": "one",
                "landing_url": "https://x.com",
                "extraction_goal": "g",
                "expected": {"headline_max_chars": 5},
            }
        ],
    }
    path = tmp_path / "ds.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")

    exit_code, results = await run(
        dataset_path=path, dry_run=True, canned_research=_CANNED
    )
    assert exit_code == 1
    assert results[0].passed is False
