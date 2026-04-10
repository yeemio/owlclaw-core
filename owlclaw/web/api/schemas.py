"""Shared API response schemas."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated response envelope."""

    items: list[T]
    total: int
    offset: int
    limit: int


class ErrorDetail(BaseModel):
    """Machine-readable error object."""

    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Top-level error response envelope."""

    error: ErrorDetail


class HealthStatusResponse(BaseModel):
    """Serialized health check row."""

    component: str
    healthy: bool
    latency_ms: float | None = None
    message: str | None = None


class OverviewMetricsResponse(BaseModel):
    """Serialized overview metrics payload."""

    total_cost_today: Decimal
    total_executions_today: int
    success_rate_today: float
    active_agents: int
    health_checks: list[HealthStatusResponse]

