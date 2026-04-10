"""Circuit breaker constraint: hide capability after consecutive failures."""

from datetime import datetime, timezone
from enum import Enum

from owlclaw.governance.ledger import Ledger, LedgerQueryFilters
from owlclaw.governance.visibility import CapabilityView, FilterResult, RunContext


class CircuitState(Enum):
    """Circuit breaker state."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerConstraint:
    """Evaluates visibility based on recent consecutive failures."""

    def __init__(self, ledger: Ledger, config: dict | None = None) -> None:
        config = config or {}
        self.ledger = ledger
        self.failure_threshold = config.get("failure_threshold", 5)
        self.recovery_timeout = config.get("recovery_timeout", 300)
        self._states: dict[tuple[str, str], tuple[CircuitState, datetime | None]] = {}

    async def evaluate(
        self,
        capability: CapabilityView,
        agent_id: str,
        context: RunContext,
    ) -> FilterResult:
        """Allow capability unless circuit is open or recent failures exceed threshold."""
        key = (agent_id, capability.name)
        state, open_time = self._states.get(key, (CircuitState.CLOSED, None))

        if state == CircuitState.OPEN:
            if open_time is not None:
                elapsed = (
                    datetime.now(timezone.utc) - open_time
                ).total_seconds()
                if elapsed > self.recovery_timeout:
                    self._states[key] = (CircuitState.HALF_OPEN, None)
                    return FilterResult(visible=True)
            return FilterResult(
                visible=False,
                reason="Circuit open (capability recently failed)",
            )

        recent_failures = await self._get_recent_failures(
            context.tenant_id, agent_id, capability.name
        )
        if recent_failures >= self.failure_threshold:
            self._states[key] = (
                CircuitState.OPEN,
                datetime.now(timezone.utc),
            )
            return FilterResult(
                visible=False,
                reason=(
                    f"Circuit open ({recent_failures} consecutive failures)"
                ),
            )
        return FilterResult(visible=True)

    async def _get_recent_failures(
        self, tenant_id: str, agent_id: str, capability_name: str
    ) -> int:
        """Count consecutive failures from most recent records."""
        records = await self.ledger.query_records(
            tenant_id=tenant_id,
            filters=LedgerQueryFilters(
                agent_id=agent_id,
                capability_name=capability_name,
                limit=self.failure_threshold + 1,
                order_by="created_at DESC",
            ),
        )
        failures = 0
        for rec in records:
            if rec.status in ("error", "timeout"):
                failures += 1
            else:
                break
        return failures

    async def on_capability_success(
        self, agent_id: str, capability_name: str
    ) -> None:
        """Reset circuit to CLOSED after a successful call."""
        key = (agent_id, capability_name)
        if key in self._states:
            self._states[key] = (CircuitState.CLOSED, None)
