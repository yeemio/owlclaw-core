"""Settings API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from owlclaw.web.api.deps import get_settings_provider, get_tenant_id
from owlclaw.web.contracts import SettingsProvider

router = APIRouter()
tenant_id_dep = Depends(get_tenant_id)
settings_provider_dep = Depends(get_settings_provider)


@router.get("/settings")
async def get_settings(
    tenant_id: str = tenant_id_dep,
    provider: SettingsProvider = settings_provider_dep,
) -> dict[str, Any]:
    """Return settings payload merged with system information."""
    settings_payload = await provider.get_settings(tenant_id=tenant_id)
    system_info = await provider.get_system_info()
    return {
        **settings_payload,
        "system": system_info,
    }
