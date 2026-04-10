"""Dependency injection registry for console API providers."""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from fastapi import Header, HTTPException, Request

from owlclaw.web.contracts import (
    AgentsProvider,
    CapabilitiesProvider,
    GovernanceProvider,
    LedgerProvider,
    OverviewProvider,
    SettingsProvider,
    TriggersProvider,
)

_PROVIDERS: dict[str, Any] = {}
logger = logging.getLogger(__name__)


def set_providers(**providers: Any) -> None:
    """Register provider instances used by FastAPI dependencies."""
    _PROVIDERS.update(providers)


def clear_providers() -> None:
    """Clear provider registry, mainly for tests."""
    _PROVIDERS.clear()


def _get_provider(name: str) -> Any:
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise RuntimeError(f"Provider '{name}' is not registered.")
    return provider


async def get_overview_provider() -> OverviewProvider:
    return cast(OverviewProvider, _get_provider("overview"))


async def get_governance_provider() -> GovernanceProvider:
    return cast(GovernanceProvider, _get_provider("governance"))


async def get_triggers_provider() -> TriggersProvider:
    return cast(TriggersProvider, _get_provider("triggers"))


async def get_agents_provider() -> AgentsProvider:
    return cast(AgentsProvider, _get_provider("agents"))


async def get_capabilities_provider() -> CapabilitiesProvider:
    return cast(CapabilitiesProvider, _get_provider("capabilities"))


async def get_ledger_provider() -> LedgerProvider:
    return cast(LedgerProvider, _get_provider("ledger"))


async def get_settings_provider() -> SettingsProvider:
    return cast(SettingsProvider, _get_provider("settings"))


def resolve_tenant_id(
    *,
    tenant_header: str | None,
    auth_tenant_id: str | None,
) -> str:
    """Resolve tenant id with authenticated context priority.

    Security policy:
    - If authenticated tenant context exists, always use it.
    - If auth is required but tenant context is missing, reject non-default
      header tenant values to avoid cross-tenant header spoofing.
    - Otherwise, keep backward-compatible header fallback.
    """
    if isinstance(auth_tenant_id, str) and auth_tenant_id.strip():
        return auth_tenant_id.strip()

    requested_tenant = ""
    if isinstance(tenant_header, str):
        requested_tenant = tenant_header.strip()
    if not requested_tenant:
        return "default"

    require_auth = os.getenv("OWLCLAW_REQUIRE_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}
    if require_auth and requested_tenant != "default":
        logger.warning(
            "Rejected tenant header without authenticated tenant context: %s",
            requested_tenant,
        )
        raise HTTPException(status_code=403, detail="Tenant must be derived from authenticated context")
    return requested_tenant


async def get_tenant_id(
    request: Request,
    x_owlclaw_tenant: str | None = Header(default=None),
) -> str:
    """Extract tenant id from request header with default fallback.

    Note:
        This header-based fallback is suitable for single-tenant/self-hosted usage.
        In multi-tenant production deployments, tenant_id should come from
        authenticated request context, not directly from client headers.
    """
    auth_tenant = getattr(request.state, "auth_tenant_id", None)
    return resolve_tenant_id(
        tenant_header=x_owlclaw_tenant,
        auth_tenant_id=auth_tenant if isinstance(auth_tenant, str) else None,
    )

