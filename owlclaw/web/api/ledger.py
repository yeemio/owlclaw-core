"""Ledger API endpoints."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from owlclaw.web.api.deps import get_ledger_provider, get_tenant_id
from owlclaw.web.api.schemas import PaginatedResponse
from owlclaw.web.contracts import LedgerProvider

router = APIRouter()
tenant_id_dep = Depends(get_tenant_id)
ledger_provider_dep = Depends(get_ledger_provider)

agent_id_query = Query(default=None)
capability_name_query = Query(default=None)
status_query = Query(default=None)
start_date_query = Query(default=None)
end_date_query = Query(default=None)
min_cost_query = Query(default=None)
max_cost_query = Query(default=None)
limit_query = Query(default=50, ge=1, le=200)
offset_query = Query(default=0, ge=0)
order_by_query = Query(default="created_at_desc", pattern="^(created_at_desc|created_at_asc|cost_desc|cost_asc)$")


@router.get("/ledger")
async def list_ledger_records(
    agent_id: str | None = agent_id_query,
    capability_name: str | None = capability_name_query,
    status: str | None = status_query,
    start_date: date | None = start_date_query,
    end_date: date | None = end_date_query,
    min_cost: Decimal | None = min_cost_query,
    max_cost: Decimal | None = max_cost_query,
    limit: int = limit_query,
    offset: int = offset_query,
    order_by: str = order_by_query,
    tenant_id: str = tenant_id_dep,
    provider: LedgerProvider = ledger_provider_dep,
) -> PaginatedResponse[dict[str, Any]]:
    """Return paginated ledger records with filtering."""
    items, total = await provider.query_records(
        tenant_id=tenant_id,
        agent_id=agent_id,
        capability_name=capability_name,
        status=status,
        start_date=start_date,
        end_date=end_date,
        min_cost=min_cost,
        max_cost=max_cost,
        limit=limit,
        offset=offset,
        order_by=order_by,
    )
    return PaginatedResponse[dict[str, Any]](items=items, total=total, offset=offset, limit=limit)


@router.get("/ledger/{record_id}")
async def get_ledger_record_detail(
    record_id: str,
    tenant_id: str = tenant_id_dep,
    provider: LedgerProvider = ledger_provider_dep,
) -> dict[str, Any]:
    """Return one ledger record detail payload."""
    detail = await provider.get_record_detail(record_id=record_id, tenant_id=tenant_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Ledger record not found")
    return detail
