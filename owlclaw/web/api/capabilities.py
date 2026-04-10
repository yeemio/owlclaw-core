"""Capabilities API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from owlclaw.web.api.deps import get_capabilities_provider, get_tenant_id
from owlclaw.web.contracts import CapabilitiesProvider

router = APIRouter()
tenant_id_dep = Depends(get_tenant_id)
capabilities_provider_dep = Depends(get_capabilities_provider)

category_query = Query(default=None, pattern="^(handler|skill|binding)?$")


@router.get("/capabilities")
async def list_capabilities(
    category: str | None = category_query,
    tenant_id: str = tenant_id_dep,
    provider: CapabilitiesProvider = capabilities_provider_dep,
) -> dict[str, list[dict[str, Any]]]:
    """Return capabilities grouped/filterable by category."""
    items = await provider.list_capabilities(tenant_id=tenant_id, category=category)
    return {"items": items}


@router.get("/capabilities/{name}/schema")
async def get_capability_schema(
    name: str,
    provider: CapabilitiesProvider = capabilities_provider_dep,
) -> dict[str, Any]:
    """Return JSON schema for one capability."""
    schema = await provider.get_capability_schema(capability_name=name)
    if schema is None:
        raise HTTPException(status_code=404, detail="Capability schema not found")
    return schema
