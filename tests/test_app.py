from app import build_options
from tools.mcp_server import SCRAPE_URL_TOOL, adpipeline_server


def test_build_options_wires_tools_agents_and_mcp_servers(monkeypatch):
    monkeypatch.setenv("PIPEBOARD_OAUTH_TOKEN", "pipeboard-test-token")

    options = build_options("resume-123")

    assert SCRAPE_URL_TOOL in options.allowed_tools
    assert "Agent" in options.allowed_tools
    assert options.resume == "resume-123"
    assert options.permission_mode == "bypassPermissions"
    assert options.can_use_tool is not None
    assert set(options.agents.keys()) == {"creative-director", "media-buyer"}
    assert options.mcp_servers["adpipeline"] is adpipeline_server
    assert options.mcp_servers["pipeboard"]["url"] == "https://meta-ads.mcp.pipeboard.co/"
    assert (
        options.mcp_servers["pipeboard"]["headers"]["Authorization"]
        == "Bearer pipeboard-test-token"
    )
