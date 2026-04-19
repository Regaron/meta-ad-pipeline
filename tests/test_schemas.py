import pydantic
import pytest

from tools.schemas import BrandResearch, RenderedCreative


_SAMPLE_RESEARCH = {
    "source_url": "https://acme.com",
    "identity": {
        "logo_url": "https://acme.com/logo.svg",
        "primary_color_hexes": ["#0F62FE", "#161616"],
    },
    "value_prop": {
        "headline": "Premium widgets engineered to last.",
        "top_3_benefits": [
            "Free shipping",
            "Lifetime warranty",
            "30-day returns",
        ],
    },
    "visual_asset_urls": [
        "https://acme.com/img/hero.jpg",
        "https://acme.com/img/lifestyle1.jpg",
    ],
    "tone_adjectives": ["confident", "warm", "playful"],
    "cta_button_text": "Get Started",
    "creative_copy_idea": {
        "hook": "Tired of widgets that break?",
        "body": "Our widgets are built from aerospace-grade materials.",
        "headline": "Widgets That Outlast You",
    },
}


def test_brand_research_validates_full_payload():
    research = BrandResearch.model_validate(_SAMPLE_RESEARCH)
    assert research.source_url == "https://acme.com"
    assert research.identity.logo_url == "https://acme.com/logo.svg"
    assert research.identity.primary_color_hexes == ["#0F62FE", "#161616"]
    assert research.value_prop.headline.startswith("Premium widgets")
    assert research.value_prop.top_3_benefits == [
        "Free shipping",
        "Lifetime warranty",
        "30-day returns",
    ]
    assert len(research.visual_asset_urls) == 2
    assert research.tone_adjectives == ["confident", "warm", "playful"]
    assert research.cta_button_text == "Get Started"
    assert research.creative_copy_idea.headline == "Widgets That Outlast You"


def test_brand_research_allows_null_logo_and_empty_assets():
    payload = dict(_SAMPLE_RESEARCH)
    payload["identity"] = {"logo_url": None, "primary_color_hexes": ["#000000"]}
    payload["visual_asset_urls"] = []
    research = BrandResearch.model_validate(payload)
    assert research.identity.logo_url is None
    assert research.visual_asset_urls == []


def test_tone_adjectives_must_be_exactly_three():
    payload = dict(_SAMPLE_RESEARCH)
    payload["tone_adjectives"] = ["confident", "warm"]
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)

    payload["tone_adjectives"] = ["a", "b", "c", "d"]
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)


def test_top_3_benefits_must_be_exactly_three():
    payload = dict(_SAMPLE_RESEARCH)
    payload["value_prop"] = {"headline": "Premium widgets", "top_3_benefits": ["One", "Two"]}
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)


def test_visual_assets_capped_at_three():
    payload = dict(_SAMPLE_RESEARCH)
    payload["visual_asset_urls"] = [f"https://acme.com/{index}.jpg" for index in range(4)]
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)


def test_primary_color_hexes_require_hex_values():
    payload = dict(_SAMPLE_RESEARCH)
    payload["identity"] = {"logo_url": None, "primary_color_hexes": ["not-a-hex"]}
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)


def test_primary_color_hexes_require_one_to_three_entries():
    payload = dict(_SAMPLE_RESEARCH)
    payload["identity"] = {"logo_url": None, "primary_color_hexes": []}
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)

    payload["identity"] = {
        "logo_url": None,
        "primary_color_hexes": ["#000", "#111", "#222", "#333"],
    }
    with pytest.raises(pydantic.ValidationError):
        BrandResearch.model_validate(payload)


def test_rendered_creative_round_trip():
    creative = RenderedCreative(
        variant_id="abc123",
        variant_note="bold typographic",
        png_url="https://ad-images-tigris.t3.tigrisfiles.io/creatives/abc123.png",
    )
    assert creative.variant_id == "abc123"
    assert creative.variant_note == "bold typographic"
    assert creative.png_url.endswith("/creatives/abc123.png")
