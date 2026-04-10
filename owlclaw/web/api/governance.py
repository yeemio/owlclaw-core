"""Governance API endpoints."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from owlclaw.web.api.deps import get_governance_provider, get_tenant_id
from owlclaw.web.contracts import GovernanceProvider

router = APIRouter()
tenant_id_dep = Depends(get_tenant_id)
governance_provider_dep = Depends(get_governance_provider)
start_date_query = Query(default=None)
end_date_query = Query(default=None)
granularity_query = Query(default="day", pattern="^(day|week|month)$")
agent_id_query = Query(default=None)


@router.get("/governance/budget")
async def get_budget_trend(
    start_date: date | None = start_date_query,
    end_date: date | None = end_date_query,
    granularity: str = granularity_query,
    tenant_id: str = tenant_id_dep,
    provider: GovernanceProvider = governance_provider_dep,
) -> dict[str, object]:
    """Return governance budget trend data."""
    today = datetime.now(timezone.utc).date()
    resolved_end = end_date or today
    resolved_start = start_date or (resolved_end - timedelta(days=6))
    rows = await provider.get_budget_trend(
        tenant_id=tenant_id,
        start_date=resolved_start,
        end_date=resolved_end,
        granularity=granularity,
    )
    return {
        "start_date": resolved_start.isoformat(),
        "end_date": resolved_end.isoformat(),
        "granularity": granularity,
        "items": rows,
    }


@router.get("/governance/circuit-breakers")
async def get_circuit_breakers(
    tenant_id: str = tenant_id_dep,
    provider: GovernanceProvider = governance_provider_dep,
) -> dict[str, object]:
    """Return circuit-breaker states for capabilities."""
    rows = await provider.get_circuit_breaker_states(tenant_id=tenant_id)
    return {"items": rows}


@router.get("/governance/visibility-matrix")
async def get_visibility_matrix(
    agent_id: str | None = agent_id_query,
    tenant_id: str = tenant_id_dep,
    provider: GovernanceProvider = governance_provider_dep,
) -> dict[str, object]:
    """Return capability visibility matrix for one tenant/agent."""
    return await provider.get_visibility_matrix(tenant_id=tenant_id, agent_id=agent_id)
