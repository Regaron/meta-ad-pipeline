from app import _extract_tigris_urls, build_options
from tools.mcp_server import SCRAPE_URL_TOOL, adpipeline_server


def test_build_options_wires_tools_agents_and_mcp_servers(monkeypatch):
    monkeypatch.setenv("PIPEBOARD_OAUTH_TOKEN", "pipeboard-test-token")

    options = build_options("resume-123")

    assert options.allowed_tools == ["Agent", SCRAPE_URL_TOOL]
    assert options.resume == "resume-123"
    assert set(options.agents.keys()) == {"creative-director", "media-buyer"}
    assert options.mcp_servers["adpipeline"] is adpipeline_server
    assert options.mcp_servers["pipeboard"]["url"] == "https://meta-ads.mcp.pipeboard.co/"
    assert (
        options.mcp_servers["pipeboard"]["headers"]["Authorization"]
        == "Bearer pipeboard-test-token"
    )


def test_extract_tigris_urls_uses_public_host_and_dedupes(monkeypatch):
    monkeypatch.setenv("TIGRIS_PUBLIC_HOST", "cdn.example.test")

    text = (
        "https://bucket.cdn.example.test/creatives/abc123.png "
        "https://bucket.cdn.example.test/creatives/abc123.png "
        "https://bucket.t3.storage.dev/creatives/def456.png "
        "https://other.cdn.example.test/creatives/ghi789.png"
    )

    assert _extract_tigris_urls(text) == [
        "https://bucket.cdn.example.test/creatives/abc123.png",
        "https://other.cdn.example.test/creatives/ghi789.png",
    ]
