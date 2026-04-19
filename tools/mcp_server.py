from claude_agent_sdk import create_sdk_mcp_server

from tools.creative import render_creative
from tools.scrape import scrape_url
from tools.view_reference import view_brand_reference

SERVER_NAME = "adpipeline"

adpipeline_server = create_sdk_mcp_server(
    name=SERVER_NAME,
    version="0.1.0",
    tools=[scrape_url, render_creative, view_brand_reference],
)

SCRAPE_URL_TOOL = f"mcp__{SERVER_NAME}__scrape_url"
RENDER_CREATIVE_TOOL = f"mcp__{SERVER_NAME}__render_creative"
VIEW_BRAND_REFERENCE_TOOL = f"mcp__{SERVER_NAME}__view_brand_reference"
