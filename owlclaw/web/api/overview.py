"""Overview API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from owlclaw.web.api.deps import get_overview_provider, get_tenant_id
from owlclaw.web.api.schemas import HealthStatusResponse, OverviewMetricsResponse
from owlclaw.web.contracts import OverviewProvider

router = APIRouter()
tenant_id_dep = Depends(get_tenant_id)
overview_provider_dep = Depends(get_overview_provider)


@router.get("/overview", response_model=OverviewMetricsResponse)
async def get_overview(
    tenant_id: str = tenant_id_dep,
    provider: OverviewProvider = overview_provider_dep,
) -> OverviewMetricsResponse:
    """Return high-level health and runtime metrics."""
    metrics = await provider.get_overview(tenant_id=tenant_id)
    return OverviewMetricsResponse(
        total_cost_today=metrics.total_cost_today,
        total_executions_today=metrics.total_executions_today,
        success_rate_today=metrics.success_rate_today,
        active_agents=metrics.active_agents,
        health_checks=[
            HealthStatusResponse(
                component=item.component,
                healthy=item.healthy,
                latency_ms=item.latency_ms,
                message=item.message,
            )
            for item in metrics.health_checks
        ],
    )
