"""WebSocket endpoints for console realtime updates."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from owlclaw.web.api.deps import (
    get_ledger_provider,
    get_overview_provider,
    resolve_tenant_id,
    get_triggers_provider,
)

router = APIRouter()


class _ConnectionLimiter:
    def __init__(self, max_connections: int) -> None:
        self._max_connections = max_connections
        self._active: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> bool:
        async with self._lock:
            if len(self._active) >= self._max_connections:
                return False
            self._active.add(websocket)
            return True

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._active.discard(websocket)


def _resolve_limiter(websocket: WebSocket) -> _ConnectionLimiter:
    app = websocket.app
    max_connections = int(getattr(app.state, "ws_max_connections", 10))
    limiter = getattr(app.state, "ws_connection_limiter", None)
    if isinstance(limiter, _ConnectionLimiter):
        return limiter
    limiter = _ConnectionLimiter(max_connections=max_connections)
    app.state.ws_connection_limiter = limiter
    return limiter


def _is_ws_authorized(websocket: WebSocket) -> bool:
    expected = os.getenv("OWLCLAW_CONSOLE_API_TOKEN", "").strip()
    if not expected:
        expected = os.getenv("OWLCLAW_CONSOLE_TOKEN", "").strip()
    if not expected:
        return True
    provided = websocket.query_params.get("token", "").strip()
    if not provided:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            provided = auth_header[7:].strip()
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


async def _stream_messages(
    *,
    websocket: WebSocket,
    tenant_id: str,
    overview_provider: Any,
    triggers_provider: Any,
    ledger_provider: Any,
) -> AsyncIterator[dict[str, Any]]:
    overview = await overview_provider.get_overview(tenant_id=tenant_id)
    yield {
        "type": "overview",
        "payload": {
            "total_cost_today": str(overview.total_cost_today),
            "total_executions_today": overview.total_executions_today,
            "success_rate_today": overview.success_rate_today,
            "active_agents": overview.active_agents,
        },
    }

    triggers = await triggers_provider.list_triggers(tenant_id=tenant_id)
    yield {
        "type": "triggers",
        "payload": {
            "items": triggers[:10],
        },
    }

    ledger_items, _total = await ledger_provider.query_records(
        tenant_id=tenant_id,
        agent_id=None,
        capability_name=None,
        status=None,
        start_date=None,
        end_date=None,
        min_cost=None,
        max_cost=None,
        limit=1,
        offset=0,
        order_by="created_at_desc",
    )
    yield {
        "type": "ledger",
        "payload": {
            "latest": ledger_items[0] if ledger_items else None,
        },
    }

    interval = float(getattr(websocket.app.state, "ws_push_interval_seconds", 30.0))
    while True:
        await asyncio.sleep(interval)
        overview = await overview_provider.get_overview(tenant_id=tenant_id)
        yield {
            "type": "overview",
            "payload": {
                "total_cost_today": str(overview.total_cost_today),
                "total_executions_today": overview.total_executions_today,
                "success_rate_today": overview.success_rate_today,
                "active_agents": overview.active_agents,
            },
        }


@router.websocket("/ws")
async def ws_stream(websocket: WebSocket) -> None:
    """WebSocket stream for overview/triggers/ledger realtime messages."""
    if not _is_ws_authorized(websocket):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    limiter = _resolve_limiter(websocket)
    accepted = await limiter.register(websocket)
    if not accepted:
        await websocket.close(code=4409)
        return

    stream: AsyncIterator[dict[str, Any]] | None = None
    try:
        auth_tenant = getattr(websocket.state, "auth_tenant_id", None)
        try:
            tenant_id = resolve_tenant_id(
                tenant_header=websocket.headers.get("x-owlclaw-tenant"),
                auth_tenant_id=auth_tenant if isinstance(auth_tenant, str) else None,
            )
        except HTTPException:
            await websocket.close(code=4403)
            return
        overview_provider = await get_overview_provider()
        triggers_provider = await get_triggers_provider()
        ledger_provider = await get_ledger_provider()
        stream = _stream_messages(
            websocket=websocket,
            tenant_id=tenant_id,
            overview_provider=overview_provider,
            triggers_provider=triggers_provider,
            ledger_provider=ledger_provider,
        )
        async for message in stream:
            await websocket.send_json(message)
    except WebSocketDisconnect:
        return
    finally:
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.aclose()  # type: ignore[has-type]
        await limiter.unregister(websocket)
