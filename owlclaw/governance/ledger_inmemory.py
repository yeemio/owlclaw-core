"""In-memory ledger for Lite Mode — no database required.

Drop-in replacement for :class:`Ledger` that stores execution records
in a plain list.  Suitable for development, testing, and Lite Mode
where PostgreSQL is not available.

Records are lost on process exit.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from owlclaw.governance.ledger import CostSummary, LedgerQueryFilters

logger = logging.getLogger(__name__)


@dataclass
class InMemoryRecord:
    """Lightweight record without SQLAlchemy ORM dependency."""

    id: uuid.UUID
    tenant_id: str
    agent_id: str
    run_id: str
    capability_name: str
    task_type: str
    input_params: dict[str, Any]
    output_result: dict[str, Any] | None
    decision_reasoning: str | None
    execution_time_ms: int
    llm_model: str
    llm_tokens_input: int
    llm_tokens_output: int
    estimated_cost: Decimal
    status: str
    error_message: str | None
    migration_weight: int | None = None
    execution_mode: str | None = None
    risk_level: Decimal | None = None
    approval_by: str | None = None
    approval_time: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryLedger:
    """In-memory execution ledger — same public interface as :class:`Ledger`.

    Constraints (:class:`BudgetConstraint`, :class:`RateLimitConstraint`,
    :class:`CircuitBreakerConstraint`) call ``query_records`` and
    ``get_cost_summary`` on the ledger; this class implements both using
    a simple list with linear scans.

    The ``record_execution`` method is synchronous (no background writer
    needed) but keeps the same ``async`` signature for compatibility.
    """

    def __init__(self, *, max_records: int = 10_000) -> None:
        self._records: list[InMemoryRecord] = []
        self._max_records = max_records

    async def start(self) -> None:
        """No-op — no background writer needed."""

    async def stop(self) -> None:
        """No-op."""

    async def record_execution(
        self,
        tenant_id: str,
        agent_id: str,
        run_id: str,
        capability_name: str,
        task_type: str,
        input_params: dict[str, Any],
        output_result: dict[str, Any] | None,
        decision_reasoning: str | None,
        execution_time_ms: int,
        llm_model: str,
        llm_tokens_input: int,
        llm_tokens_output: int,
        estimated_cost: Decimal,
        status: str,
        error_message: str | None = None,
        migration_weight: int | None = None,
        execution_mode: str | None = None,
        risk_level: Decimal | None = None,
        approval_by: str | None = None,
        approval_time: datetime | None = None,
    ) -> None:
        """Store one execution record in memory."""
        def _require(value: Any, name: str) -> str:
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a non-empty string")
            v = value.strip()
            if not v:
                raise ValueError(f"{name} must be a non-empty string")
            return v

        record = InMemoryRecord(
            id=uuid.uuid4(),
            tenant_id=_require(tenant_id, "tenant_id"),
            agent_id=_require(agent_id, "agent_id"),
            run_id=_require(run_id, "run_id"),
            capability_name=_require(capability_name, "capability_name"),
            task_type=_require(task_type, "task_type"),
            input_params=input_params if isinstance(input_params, dict) else {},
            output_result=output_result,
            decision_reasoning=decision_reasoning,
            execution_time_ms=execution_time_ms,
            llm_model=llm_model,
            llm_tokens_input=llm_tokens_input,
            llm_tokens_output=llm_tokens_output,
            estimated_cost=estimated_cost,
            status=status,
            error_message=error_message,
            migration_weight=migration_weight,
            execution_mode=execution_mode,
            risk_level=risk_level,
            approval_by=approval_by,
            approval_time=approval_time,
        )
        self._records.append(record)
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records:]

    async def query_records(
        self,
        tenant_id: str,
        filters: LedgerQueryFilters,
    ) -> list[InMemoryRecord]:
        """Query records with the same filter semantics as the DB-backed Ledger."""
        results = [r for r in self._records if r.tenant_id == tenant_id]

        if filters.agent_id is not None:
            results = [r for r in results if r.agent_id == filters.agent_id]
        if filters.capability_name is not None:
            results = [r for r in results if r.capability_name == filters.capability_name]
        if filters.status is not None:
            results = [r for r in results if r.status == filters.status]
        if filters.execution_mode is not None:
            results = [r for r in results if r.execution_mode == filters.execution_mode]
        if filters.start_date is not None:
            start_dt = datetime.combine(filters.start_date, time.min, tzinfo=timezone.utc)
            results = [r for r in results if r.created_at >= start_dt]
        if filters.end_date is not None:
            end_dt = datetime.combine(filters.end_date, time.max, tzinfo=timezone.utc)
            results = [r for r in results if r.created_at <= end_dt]

        if filters.order_by == "created_at DESC":
            results.sort(key=lambda r: r.created_at, reverse=True)
        elif filters.order_by == "created_at ASC":
            results.sort(key=lambda r: r.created_at)

        if filters.offset is not None:
            results = results[filters.offset:]
        if filters.limit is not None:
            results = results[:filters.limit]

        return results

    async def get_cost_summary(
        self,
        tenant_id: str,
        agent_id: str,
        start_date: date,
        end_date: date,
    ) -> CostSummary:
        """Sum estimated_cost for records in the date range."""
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)

        matching = [
            r for r in self._records
            if r.tenant_id == tenant_id
            and r.agent_id == agent_id
            and start_dt <= r.created_at <= end_dt
        ]

        total = sum((r.estimated_cost for r in matching), Decimal("0"))
        by_capability: dict[str, Decimal] = {}
        for r in matching:
            by_capability[r.capability_name] = (
                by_capability.get(r.capability_name, Decimal("0")) + r.estimated_cost
            )

        return CostSummary(
            total_cost=total,
            by_agent={agent_id: total},
            by_capability=by_capability,
        )
