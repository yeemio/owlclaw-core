"""MCP tool bindings for governance observability endpoints."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from owlclaw.capabilities.registry import CapabilityRegistry
from owlclaw.governance.ledger import LedgerQueryFilters


class GovernanceLedger(Protocol):
    """Ledger protocol required by governance MCP tools."""

    async def get_cost_summary(
        self,
        tenant_id: str,
        agent_id: str,
        start_date: date,
        end_date: date,
    ) -> Any:
        """Return a cost summary object exposing ``total_cost``."""

    async def query_records(self, tenant_id: str, filters: LedgerQueryFilters) -> list[Any]:
        """Return ledger records matching the provided filters."""


RateLimitProvider = Callable[[str], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class _BudgetDefaults:
    daily_limit: str
    monthly_limit: str


def register_governance_mcp_tools(
    *,
    registry: CapabilityRegistry,
    ledger: GovernanceLedger,
    default_tenant_id: str = "default",
    default_agent_id: str = "default",
    default_daily_limit: str = "0",
    default_monthly_limit: str = "0",
    rate_limit_provider: RateLimitProvider | None = None,
) -> None:
    """Register governance-related MCP tools into capability registry."""

    defaults = _BudgetDefaults(
        daily_limit=_normalize_decimal_str(default_daily_limit),
        monthly_limit=_normalize_decimal_str(default_monthly_limit),
    )

    async def governance_budget_status(
        tenant_id: str = default_tenant_id,
        agent_id: str = default_agent_id,
        daily_limit: str = defaults.daily_limit,
        monthly_limit: str = defaults.monthly_limit,
    ) -> dict[str, str]:
        """Query current daily/monthly budget usage for one agent."""
        today = datetime.now(timezone.utc).date()
        month_start = today.replace(day=1)
        daily_summary = await ledger.get_cost_summary(
            tenant_id=_normalize_non_empty(tenant_id, "tenant_id"),
            agent_id=_normalize_non_empty(agent_id, "agent_id"),
            start_date=today,
            end_date=today,
        )
        monthly_summary = await ledger.get_cost_summary(
            tenant_id=tenant_id,
            agent_id=agent_id,
            start_date=month_start,
            end_date=today,
        )
        return {
            "daily_used": _normalize_decimal_str(getattr(daily_summary, "total_cost", "0")),
            "daily_limit": _normalize_decimal_str(daily_limit),
            "monthly_used": _normalize_decimal_str(getattr(monthly_summary, "total_cost", "0")),
            "monthly_limit": _normalize_decimal_str(monthly_limit),
        }

    async def governance_audit_query(
        tenant_id: str = default_tenant_id,
        caller: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Query governance audit records from ledger."""
        normalized_tenant = _normalize_non_empty(tenant_id, "tenant_id")
        normalized_limit = _normalize_limit(limit)
        filters = LedgerQueryFilters(
            agent_id=_optional_non_empty(caller),
            start_date=_parse_iso_date(start_time) if start_time else None,
            end_date=_parse_iso_date(end_time) if end_time else None,
            limit=normalized_limit,
            order_by="created_at DESC",
        )
        records = await ledger.query_records(normalized_tenant, filters)
        return {
            "records": [_record_to_dict(record) for record in records[:normalized_limit]],
            "count": min(len(records), normalized_limit),
        }

    async def governance_rate_limit_status(service: str = "global") -> dict[str, Any]:
        """Query rate-limit counters for one governance service bucket."""
        normalized_service = _normalize_non_empty(service, "service")
        payload: Mapping[str, Any] = {}
        if rate_limit_provider is not None:
            provided = rate_limit_provider(normalized_service)
            if inspect.isawaitable(provided):
                provided = await provided
            payload = provided
        return {
            "service": normalized_service,
            "current_qps": _normalize_number(payload.get("current_qps", 0.0)),
            "limit_qps": _normalize_number(payload.get("limit_qps", 0.0)),
            "rejected_count": _normalize_int(payload.get("rejected_count", 0)),
        }

    registry.register_handler("governance_budget_status", governance_budget_status)
    registry.register_handler("governance_audit_query", governance_audit_query)
    registry.register_handler("governance_rate_limit_status", governance_rate_limit_status)


def _normalize_non_empty(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _optional_non_empty(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if normalized else None


def _normalize_decimal_str(value: Any) -> str:
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, ValueError):
        normalized = Decimal("0")
    return format(normalized, "f")


def _normalize_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("limit must be an integer")
    if value < 1:
        raise ValueError("limit must be >= 1")
    return min(value, 200)


def _parse_iso_date(value: str) -> date:
    text = _normalize_non_empty(value, "time")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("time must be ISO-8601 format") from exc
    return parsed.astimezone(timezone.utc).date()


def _record_to_dict(record: Any) -> dict[str, Any]:
    created_at = getattr(record, "created_at", None)
    if isinstance(created_at, datetime):
        timestamp = created_at.astimezone(timezone.utc).isoformat()
    else:
        timestamp = None
    return {
        "timestamp": timestamp,
        "caller": getattr(record, "agent_id", ""),
        "model": getattr(record, "llm_model", ""),
        "tokens": {
            "input": _normalize_int(getattr(record, "llm_tokens_input", 0)),
            "output": _normalize_int(getattr(record, "llm_tokens_output", 0)),
        },
        "cost": _normalize_decimal_str(getattr(record, "estimated_cost", "0")),
        "result": getattr(record, "status", ""),
    }


def _normalize_number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return 0.0


def _normalize_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError:
        return 0
