"""Triggers API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from owlclaw.db.exceptions import ConfigurationError

from owlclaw.web.api.deps import get_tenant_id, get_triggers_provider
from owlclaw.web.api.schemas import PaginatedResponse
from owlclaw.web.contracts import TriggersProvider

router = APIRouter()
tenant_id_dep = Depends(get_tenant_id)
triggers_provider_dep = Depends(get_triggers_provider)

limit_query = Query(default=50, ge=1, le=200)
offset_query = Query(default=0, ge=0)


@router.get("/triggers")
async def list_triggers(
    tenant_id: str = tenant_id_dep,
    provider: TriggersProvider = triggers_provider_dep,
) -> dict[str, list[dict[str, Any]]]:
    """Return unified trigger list across trigger types."""
    try:
        items = await provider.list_triggers(tenant_id=tenant_id)
    except ConfigurationError:
        return {"items": []}
    return {"items": items}


@router.get("/triggers/{trigger_id}/history")
async def get_trigger_history(
    trigger_id: str,
    limit: int = limit_query,
    offset: int = offset_query,
    tenant_id: str = tenant_id_dep,
    provider: TriggersProvider = triggers_provider_dep,
) -> PaginatedResponse[dict[str, Any]]:
    """Return paginated execution history for one trigger id."""
    try:
        items, total = await provider.get_trigger_history(
            trigger_id=trigger_id,
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
        )
    except ConfigurationError:
        return PaginatedResponse[dict[str, Any]](items=[], total=0, offset=offset, limit=limit)
    return PaginatedResponse[dict[str, Any]](items=items, total=total, offset=offset, limit=limit)
