import io
import json
import os
import platform
import uuid
from typing import Any

import boto3
import pypdfium2 as pdfium
from botocore.client import Config
from claude_agent_sdk import tool
from PIL import Image

_PNG_SIZE = (1080, 1080)

if platform.system() == "Darwin":
    existing_library_path = os.environ.get("DYLD_LIBRARY_PATH", "")
    extra_paths = [
        "/opt/homebrew/opt/glib/lib",
        "/opt/homebrew/opt/pango/lib",
        "/opt/homebrew/opt/harfbuzz/lib",
        "/opt/homebrew/opt/fontconfig/lib",
    ]
    parts = [p for p in existing_library_path.split(":") if p]
    for path in reversed(extra_paths):
        if path not in parts:
            parts.insert(0, path)
    os.environ["DYLD_LIBRARY_PATH"] = ":".join(parts)

import weasyprint


def _render_html_to_png_bytes(html: str) -> bytes:
    """Render HTML to a 1080x1080 PNG byte string."""
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    pdf = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    page = pdf[0]
    # WeasyPrint maps CSS px at 96 DPI; PDF's native DPI is 72.
    # Scale = 96/72 lifts the render back up to one pixel per CSS px.
    pil_img = page.render(scale=96 / 72).to_pil()
    if pil_img.size != _PNG_SIZE:
        pil_img = pil_img.resize(_PNG_SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_REGION", "auto"),
        config=Config(s3={"addressing_style": "virtual"}),
    )


def _upload_png(png_bytes: bytes, key: str) -> str:
    bucket = os.environ["TIGRIS_BUCKET"]
    _s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=png_bytes,
        ACL="public-read",
        ContentType="image/png",
    )
    return f"https://{bucket}.t3.storage.dev/{key}"


async def _render_handler(args: dict[str, Any]) -> dict[str, Any]:
    """Render HTML -> PNG and upload it to Tigris."""
    variant_id = uuid.uuid4().hex[:10]
    png_bytes = _render_html_to_png_bytes(args["html"])
    key = f"creatives/{variant_id}.png"
    url = _upload_png(png_bytes, key)
    payload = {
        "variant_id": variant_id,
        "variant_note": args["variant_note"],
        "png_url": url,
    }
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


render_creative = tool(
    "render_creative",
    "Render an HTML ad creative to a 1080x1080 PNG on Tigris. Returns the public URL.",
    {"html": str, "variant_note": str},
)(_render_handler)
