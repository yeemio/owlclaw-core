"""Agents API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from owlclaw.db.exceptions import ConfigurationError
from owlclaw.web.api.deps import get_agents_provider, get_tenant_id
from owlclaw.web.contracts import AgentsProvider

router = APIRouter()
tenant_id_dep = Depends(get_tenant_id)
agents_provider_dep = Depends(get_agents_provider)


@router.get("/agents")
async def list_agents(
    tenant_id: str = tenant_id_dep,
    provider: AgentsProvider = agents_provider_dep,
) -> dict[str, Any]:
    """Return agent list payload."""
    try:
        items = await provider.list_agents(tenant_id=tenant_id)
    except ConfigurationError:
        return {"items": [], "message": "Database not configured"}
    return {"items": items}


@router.get("/agents/{agent_id}")
async def get_agent_detail(
    agent_id: str,
    tenant_id: str = tenant_id_dep,
    provider: AgentsProvider = agents_provider_dep,
) -> dict[str, Any]:
    """Return one agent detail payload."""
    try:
        detail = await provider.get_agent_detail(agent_id=agent_id, tenant_id=tenant_id)
    except ConfigurationError:
        raise HTTPException(status_code=404, detail="Agent not found") from None
    if detail is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return detail
