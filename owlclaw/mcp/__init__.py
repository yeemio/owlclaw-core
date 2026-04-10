"""MCP server integration for OwlClaw."""

from owlclaw.mcp.a2a import create_agent_card_app
from owlclaw.mcp.generated_tools import register_generated_mcp_tools
from owlclaw.mcp.governance_tools import register_governance_mcp_tools
from owlclaw.mcp.http_transport import create_mcp_http_app
from owlclaw.mcp.server import McpProtocolServer
from owlclaw.mcp.task_tools import register_task_mcp_tools

__all__ = [
    "McpProtocolServer",
    "create_agent_card_app",
    "create_mcp_http_app",
    "register_generated_mcp_tools",
    "register_governance_mcp_tools",
    "register_task_mcp_tools",
]
