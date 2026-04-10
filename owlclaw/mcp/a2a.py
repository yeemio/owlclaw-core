"""A2A Agent Card HTTP endpoint helpers."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from owlclaw.mcp.server import McpProtocolServer


def create_agent_card_app(
    *,
    server: McpProtocolServer,
    url: str = "http://localhost:8080",
    name: str = "OwlClaw",
    description: str = "AI-powered business system intelligence",
    version: str = "0.1.0",
) -> Starlette:
    """Create a Starlette app exposing `/.well-known/agent.json`."""

    async def _agent_card(_: Request) -> JSONResponse:
        payload = server.build_agent_card(
            url=url,
            name=name,
            description=description,
            version=version,
        )
        return JSONResponse(payload)

    return Starlette(
        routes=[
            Route("/.well-known/agent.json", endpoint=_agent_card, methods=["GET"]),
        ]
    )
