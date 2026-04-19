from typing import Literal

from pydantic import BaseModel, Field

CallToAction = Literal[
    "LEARN_MORE",
    "SHOP_NOW",
    "SIGN_UP",
    "DOWNLOAD",
    "GET_OFFER",
    "BOOK_TRAVEL",
    "CONTACT_US",
    "SUBSCRIBE",
]


class AdCopy(BaseModel):
    """Structured ad copy extracted from a landing page."""

    headline: str = Field(..., description="Short headline, target <= 40 chars.")
    primary_text: str = Field(..., description="Ad body text, target <= 125 chars.")
    description: str = Field(..., description="Link description, target <= 30 chars.")
    value_props: list[str] = Field(..., description="3-5 short selling-point bullets.")
    call_to_action: CallToAction = Field(..., description="Facebook CTA button type.")
    brand_color_theme: str = Field(
        ...,
        description="Color theme phrase, e.g. 'warm sunset oranges', 'clean tech blue'.",
    )


class RenderedCreative(BaseModel):
    """A rendered ad creative uploaded to Tigris."""

    variant_id: str
    variant_note: str
    png_url: str
