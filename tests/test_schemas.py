from tools.schemas import AdCopy, RenderedCreative


def test_ad_copy_validates():
    ac = AdCopy(
        headline="Save on Widgets",
        primary_text="Get 20% off premium widgets today.",
        description="Shop now.",
        value_props=["Free shipping", "30-day returns", "Lifetime warranty"],
        call_to_action="SHOP_NOW",
        brand_color_theme="warm sunset oranges",
    )
    assert ac.headline == "Save on Widgets"
    assert ac.call_to_action == "SHOP_NOW"
    assert len(ac.value_props) == 3


def test_ad_copy_rejects_invalid_cta():
    import pydantic

    try:
        AdCopy(
            headline="x",
            primary_text="x",
            description="x",
            value_props=["a"],
            call_to_action="NOT_A_REAL_CTA",
            brand_color_theme="x",
        )
    except pydantic.ValidationError:
        return
    raise AssertionError("expected ValidationError for invalid CTA")


def test_rendered_creative_roundtrip():
    rc = RenderedCreative(
        variant_id="abc123",
        variant_note="bold typographic",
        png_url="https://bucket.t3.storage.dev/creatives/abc123.png",
    )
    assert rc.variant_id == "abc123"
    assert rc.png_url.startswith("https://")
