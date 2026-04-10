"""Budget constraint: hide high-cost capabilities when agent budget is exhausted."""

import asyncio
from datetime import date
from decimal import Decimal, InvalidOperation
from time import monotonic

from owlclaw.governance.ledger import Ledger
from owlclaw.governance.visibility import CapabilityView, FilterResult, RunContext


class BudgetConstraint:
    """Evaluates visibility based on agent budget and capability cost."""

    def __init__(self, ledger: Ledger, config: dict | None = None) -> None:
        config = config or {}
        self.ledger = ledger
        self.high_cost_threshold = self._safe_decimal(
            config.get("high_cost_threshold"),
            default=Decimal("0.1"),
        )
        self.budget_limits: dict[str, str | Decimal] = config.get(
            "budget_limits", {}
        )
        self.reservation_ttl_seconds = int(config.get("reservation_ttl_seconds", 30))
        self._lock = asyncio.Lock()
        self._reservations: dict[tuple[str, str], list[tuple[float, Decimal]]] = {}

    @staticmethod
    def _safe_decimal(value: object, *, default: Decimal) -> Decimal:
        if value is None:
            return default
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return default

    async def evaluate(
        self,
        capability: CapabilityView,
        agent_id: str,
        context: RunContext,
    ) -> FilterResult:
        """Allow capability if budget is sufficient or capability is low-cost."""
        budget_limit = self.budget_limits.get(agent_id)
        if not budget_limit:
            return FilterResult(visible=True)

        start_of_month = date.today().replace(day=1)
        end_date = date.today()
        cost_summary = await self.ledger.get_cost_summary(
            tenant_id=context.tenant_id,
            agent_id=agent_id,
            start_date=start_of_month,
            end_date=end_date,
        )
        used_cost = cost_summary.total_cost
        limit_decimal = self._safe_decimal(budget_limit, default=Decimal("0"))
        estimated_cost = self._estimate_capability_cost(capability)
        reservation_needed = estimated_cost > self.high_cost_threshold

        async with self._lock:
            key = (context.tenant_id, agent_id)
            self._purge_expired_reservations(key)
            reserved = sum(amount for _, amount in self._reservations.get(key, []))
            remaining = limit_decimal - used_cost - reserved
            if reservation_needed and (remaining <= 0 or remaining < estimated_cost):
                return FilterResult(
                    visible=False,
                    reason=(
                        f"Budget exhausted (used {used_cost}, reserved {reserved}, limit {budget_limit})"
                    ),
                )
            if reservation_needed:
                self._reservations.setdefault(key, []).append(
                    (monotonic() + float(self.reservation_ttl_seconds), estimated_cost)
                )
        return FilterResult(visible=True)

    async def refund_reservation(self, tenant_id: str, agent_id: str, amount: Decimal) -> None:
        """Release one reservation amount for requests that were not executed."""
        safe_amount = self._safe_decimal(amount, default=Decimal("0"))
        if safe_amount <= 0:
            return
        async with self._lock:
            key = (tenant_id, agent_id)
            self._purge_expired_reservations(key)
            entries = self._reservations.get(key, [])
            for idx in range(len(entries) - 1, -1, -1):
                _, reserved_amount = entries[idx]
                if reserved_amount >= safe_amount:
                    entries.pop(idx)
                    break
            if not entries:
                self._reservations.pop(key, None)

    def _purge_expired_reservations(self, key: tuple[str, str]) -> None:
        now = monotonic()
        entries = self._reservations.get(key)
        if not entries:
            return
        kept = [entry for entry in entries if entry[0] > now]
        if kept:
            self._reservations[key] = kept
            return
        self._reservations.pop(key, None)

    def _estimate_capability_cost(self, capability: CapabilityView) -> Decimal:
        """Estimate single-call cost from metadata or default."""
        owlclaw = capability.metadata.get("owlclaw") or {}
        constraints = owlclaw.get("constraints") or {}
        raw = constraints.get("estimated_cost")
        if raw is not None:
            return self._safe_decimal(raw, default=Decimal("0.05"))
        return Decimal("0.05")
