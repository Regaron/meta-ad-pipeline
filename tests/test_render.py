import asyncio
import io
import json
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws
from PIL import Image

FIXTURE_HTML = (Path(__file__).parent / "fixtures" / "sample_creative.html").read_text()


@pytest.fixture(autouse=True)
def _tigris_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://t3.storage.dev")
    monkeypatch.setenv("AWS_REGION", "auto")
    monkeypatch.setenv("TIGRIS_BUCKET", "ad-pipeline-creatives")


@mock_aws
def test_render_creative_produces_1080_png_and_uploads_to_tigris(monkeypatch):
    """render_creative must: render PDF -> PNG, upload with public-read ACL, return URL."""

    async def _run() -> None:
        # Arrange: create the bucket in moto
        monkeypatch.delenv("AWS_ENDPOINT_URL")
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="ad-pipeline-creatives")
        monkeypatch.setenv("AWS_ENDPOINT_URL", "https://t3.storage.dev")

        from tools.creative import _render_handler

        # moto intercepts boto3 regardless of endpoint_url
        with patch("tools.creative._s3_client", return_value=s3):
            result = await _render_handler(
                {"html": FIXTURE_HTML, "variant_note": "bold gradient test"}
            )

        assert "content" in result
        payload = json.loads(result["content"][0]["text"])
        assert payload["variant_note"] == "bold gradient test"
        assert payload["png_url"].startswith(
            "https://ad-pipeline-creatives.t3.storage.dev/creatives/"
        )
        assert payload["png_url"].endswith(".png")

        # The object exists in the mock bucket with correct Content-Type
        key = payload["png_url"].split(".t3.storage.dev/")[1]
        head = s3.head_object(Bucket="ad-pipeline-creatives", Key=key)
        assert head["ContentType"] == "image/png"
        acl = s3.get_object_acl(Bucket="ad-pipeline-creatives", Key=key)
        assert any(
            grant.get("Permission") == "READ"
            and grant.get("Grantee", {}).get("URI")
            == "http://acs.amazonaws.com/groups/global/AllUsers"
            for grant in acl["Grants"]
        )

        # The body is a valid 1080x1080 PNG
        obj = s3.get_object(Bucket="ad-pipeline-creatives", Key=key)
        png_bytes = obj["Body"].read()
        img = Image.open(io.BytesIO(png_bytes))
        assert img.format == "PNG"
        assert img.size == (1080, 1080)

    asyncio.run(_run())
