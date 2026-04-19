from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

HexColor = Annotated[
    str,
    StringConstraints(pattern=r"^#[0-9A-Fa-f]{3,8}$"),
]


class BrandIdentity(BaseModel):
    """Visual identity scraped from a landing page."""

    logo_url: str | None = Field(
        ...,
        description="Primary logo URL on the page; null if no usable logo was found.",
    )
    primary_color_hexes: list[HexColor] = Field(
        ...,
        min_length=1,
        max_length=3,
        description="One to three primary brand hex codes.",
    )


class CoreValueProp(BaseModel):
    """Core headline and benefits extracted from the page."""

    headline: str = Field(..., description="Main headline from the landing page.")
    top_3_benefits: list[str] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exactly three short benefit statements.",
    )


class CreativeCopyIdea(BaseModel):
    """One Problem/Solution copy draft for downstream agents."""

    hook: str = Field(..., description="Relatable pain point.")
    body: str = Field(..., description="How the product solves the problem.")
    headline: str = Field(
        ...,
        description="Punchy benefit-driven headline; aim for 40 characters or fewer.",
    )


class BrandResearch(BaseModel):
    """Structured brand research produced by Browser Use Cloud."""

    source_url: str = Field(..., description="Landing page URL that was researched.")
    identity: BrandIdentity
    value_prop: CoreValueProp
    visual_asset_urls: list[str] = Field(
        ...,
        max_length=3,
        description="Up to three product or lifestyle image URLs; may be empty.",
    )
    tone_adjectives: list[str] = Field(
        ...,
        min_length=3,
        max_length=3,
        description="Exactly three adjectives describing the brand voice.",
    )
    cta_button_text: str = Field(
        ...,
        description="Literal primary CTA text found on the page.",
    )
    creative_copy_idea: CreativeCopyIdea


class RenderedCreative(BaseModel):
    """A rendered ad creative uploaded to Tigris."""

    variant_id: str
    variant_note: str
    png_url: str
