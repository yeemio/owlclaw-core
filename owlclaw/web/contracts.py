"""Protocol contracts for console backend providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Generic, Protocol, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class HealthStatus:
    """Health status of one runtime component."""

    component: str
    healthy: bool
    latency_ms: float | None = None
    message: str | None = None


@dataclass(frozen=True)
class OverviewMetrics:
    """Aggregated overview metrics for console landing page."""

    total_cost_today: Decimal
    total_executions_today: int
    success_rate_today: float
    active_agents: int
    health_checks: list[HealthStatus]


@dataclass(frozen=True)
class PaginatedResult(Generic[T]):
    """Generic paginated payload shared by providers."""

    items: list[T]
    total: int
    offset: int
    limit: int


class OverviewProvider(Protocol):
    """Provider contract for overview metrics."""

    async def get_overview(self, tenant_id: str) -> OverviewMetrics:
        """Return aggregated overview metrics for one tenant."""


class GovernanceProvider(Protocol):
    """Provider contract for governance endpoints."""

    async def get_budget_trend(
        self,
        tenant_id: str,
        start_date: date,
        end_date: date,
        granularity: str,
    ) -> list[dict[str, Any]]:
        """Return budget usage trend grouped by granularity."""

    async def get_circuit_breaker_states(self, tenant_id: str) -> list[dict[str, Any]]:
        """Return circuit-breaker state snapshots."""

    async def get_visibility_matrix(
        self,
        tenant_id: str,
        agent_id: str | None,
    ) -> dict[str, Any]:
        """Return capability visibility matrix for one tenant/agent."""


class TriggersProvider(Protocol):
    """Provider contract for trigger endpoints."""

    async def list_triggers(self, tenant_id: str) -> list[dict[str, Any]]:
        """Return trigger list in a unified schema."""

    async def get_trigger_history(
        self,
        trigger_id: str,
        tenant_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return trigger history records and total count."""


class AgentsProvider(Protocol):
    """Provider contract for agent endpoints."""

    async def list_agents(self, tenant_id: str) -> list[dict[str, Any]]:
        """Return visible agents for one tenant."""

    async def get_agent_detail(self, agent_id: str, tenant_id: str) -> dict[str, Any] | None:
        """Return one agent detail payload."""


class CapabilitiesProvider(Protocol):
    """Provider contract for capabilities endpoints."""

    async def list_capabilities(
        self,
        tenant_id: str,
        category: str | None,
    ) -> list[dict[str, Any]]:
        """Return capability list, optionally filtered by category."""

    async def get_capability_schema(self, capability_name: str) -> dict[str, Any] | None:
        """Return JSON schema for one capability."""


class LedgerProvider(Protocol):
    """Provider contract for ledger endpoints."""

    async def query_records(
        self,
        tenant_id: str,
        agent_id: str | None,
        capability_name: str | None,
        status: str | None,
        start_date: date | None,
        end_date: date | None,
        min_cost: Decimal | None,
        max_cost: Decimal | None,
        limit: int,
        offset: int,
        order_by: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return filtered ledger records and total count."""

    async def get_record_detail(self, record_id: str, tenant_id: str) -> dict[str, Any] | None:
        """Return one ledger record detail payload."""


class SettingsProvider(Protocol):
    """Provider contract for settings endpoints."""

    async def get_settings(self, tenant_id: str) -> dict[str, Any]:
        """Return runtime settings payload."""

    async def get_system_info(self) -> dict[str, Any]:
        """Return process and version metadata."""
