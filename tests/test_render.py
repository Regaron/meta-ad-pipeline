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
    monkeypatch.setenv("TIGRIS_STORAGE_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("TIGRIS_STORAGE_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("TIGRIS_STORAGE_BUCKET", "ad-images-tigris")
    monkeypatch.setenv("TIGRIS_API_ENDPOINT", "https://t3.storage.dev")
    monkeypatch.setenv("TIGRIS_PUBLIC_HOST", "t3.tigrisfiles.io")


@mock_aws
def test_render_creative_produces_1080_png_and_uploads_to_tigris(monkeypatch):
    async def _run() -> None:
        s3 = boto3.client(
            "s3",
            aws_access_key_id="test",
            aws_secret_access_key="test",
            region_name="us-east-1",
        )
        s3.create_bucket(Bucket="ad-images-tigris")

        from tools.creative import _render_handler

        with patch("tools.creative._s3_client", return_value=s3):
            result = await _render_handler(
                {"html": FIXTURE_HTML, "variant_note": "bold gradient test"}
            )

        assert "content" in result
        payload = json.loads(result["content"][0]["text"])
        assert payload["variant_note"] == "bold gradient test"
        assert payload["png_url"].startswith(
            "https://ad-images-tigris.t3.tigrisfiles.io/creatives/"
        )
        assert payload["png_url"].endswith(".png")

        key = payload["png_url"].split(".t3.tigrisfiles.io/")[1]
        head = s3.head_object(Bucket="ad-images-tigris", Key=key)
        assert head["ContentType"] == "image/png"

        acl = s3.get_object_acl(Bucket="ad-images-tigris", Key=key)
        assert any(
            grant.get("Permission") == "READ"
            and grant.get("Grantee", {}).get("URI")
            == "http://acs.amazonaws.com/groups/global/AllUsers"
            for grant in acl["Grants"]
        )

        obj = s3.get_object(Bucket="ad-images-tigris", Key=key)
        png_bytes = obj["Body"].read()
        img = Image.open(io.BytesIO(png_bytes))
        assert img.format == "PNG"
        assert img.size == (1080, 1080)

    asyncio.run(_run())
