"""Starlette HTTP transport adapter for MCP JSON-RPC requests."""

from __future__ import annotations

from json import JSONDecodeError

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from owlclaw.mcp.server import McpProtocolServer


def create_mcp_http_app(
    *,
    server: McpProtocolServer,
    include_agent_card: bool = True,
    agent_card_url: str = "http://localhost:8080",
) -> Starlette:
    """Create HTTP MCP app exposing `/mcp` and optional agent card endpoint."""

    async def _mcp_endpoint(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except JSONDecodeError:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "parse error"},
                },
                status_code=400,
            )
        if not isinstance(payload, dict):
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "request must be an object"},
                },
                status_code=400,
            )
        response = await server.handle_message(payload)
        return JSONResponse(response)

    async def _health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def _agent_card(_: Request) -> JSONResponse:
        return JSONResponse(server.build_agent_card(url=agent_card_url))

    routes = [
        Route("/mcp", endpoint=_mcp_endpoint, methods=["POST"]),
        Route("/health", endpoint=_health, methods=["GET"]),
    ]
    if include_agent_card:
        routes.append(Route("/.well-known/agent.json", endpoint=_agent_card, methods=["GET"]))
    return Starlette(routes=routes)
