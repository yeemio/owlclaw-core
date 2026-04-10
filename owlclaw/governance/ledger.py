"""Execution ledger: record and query capability runs.

Uses async queue for non-blocking writes; background batch writer
is started/stopped via start()/stop().
"""

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, DateTime, Index, Integer, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from owlclaw.db import Base

logger = logging.getLogger(__name__)


@dataclass
class LedgerQueryFilters:
    """Filters for querying ledger records."""

    agent_id: str | None = None
    capability_name: str | None = None
    status: str | None = None
    execution_mode: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    limit: int | None = None
    offset: int | None = None
    order_by: str | None = None


@dataclass
class CostSummary:
    """Aggregated cost over a period."""

    total_cost: Decimal
    by_agent: dict[str, Decimal] = field(default_factory=dict)
    by_capability: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class LedgerConfig:
    """Runtime configuration for Ledger writer behavior."""

    fallback_log_path: str = "ledger_fallback.log"


class LedgerRecord(Base):
    """Single capability execution record (audit and cost analysis)."""

    __tablename__ = "ledger_records"
    __table_args__ = (
        Index("idx_ledger_tenant_agent", "tenant_id", "agent_id"),
        Index("idx_ledger_tenant_capability", "tenant_id", "capability_name"),
        Index("idx_ledger_tenant_created", "tenant_id", "created_at"),
        Index("idx_ledger_tenant_run", "tenant_id", "run_id"),
        Index("idx_ledger_tenant_status", "tenant_id", "status"),
        Index("idx_ledger_tenant_task_type", "tenant_id", "task_type"),
        Index("idx_ledger_tenant_execution_mode", "tenant_id", "execution_mode"),
        {"comment": "Agent capability execution records for audit and cost analysis"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        primary_key=True,
    )
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[str] = mapped_column(String(255), nullable=False)
    capability_name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)

    input_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    decision_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    execution_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_model: Mapped[str] = mapped_column(String(100), nullable=False)
    llm_tokens_input: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_tokens_output: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 4),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    migration_weight: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    risk_level: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 4), nullable=True)
    approval_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approval_time: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Ledger:
    """Records capability executions via async queue and background writer."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        batch_size: int = 10,
        flush_interval: float = 5.0,
        fallback_log_path: str = "ledger_fallback.log",
        queue_maxsize: int = 10_000,
    ) -> None:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        if isinstance(flush_interval, bool) or not isinstance(flush_interval, int | float):
            raise ValueError("flush_interval must be a positive number")
        flush_interval_value = float(flush_interval)
        if flush_interval_value <= 0:
            raise ValueError("flush_interval must be a positive number")
        if not isinstance(fallback_log_path, str) or not fallback_log_path.strip():
            raise ValueError("fallback_log_path must be a non-empty string")
        if isinstance(queue_maxsize, bool) or not isinstance(queue_maxsize, int) or queue_maxsize < 1:
            raise ValueError("queue_maxsize must be a positive integer")
        self._session_factory = session_factory
        self._batch_size = batch_size
        self._flush_interval = flush_interval_value
        self._fallback_log_path = fallback_log_path.strip()
        self._flush_max_retries = 3
        self._flush_backoff_base_seconds = 0.1
        # Bounded queue to prevent unbounded memory growth under sustained DB backpressure.
        self._write_queue: asyncio.Queue[LedgerRecord] = asyncio.Queue(maxsize=queue_maxsize)
        self._writer_task: asyncio.Task[None] | None = None

    def get_readonly_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return read-only session factory view for query-only callers."""
        return self._session_factory

    async def start(self) -> None:
        """Start the background writer task."""
        if self._writer_task is not None and not self._writer_task.done():
            logger.warning("Ledger background writer already running")
            return
        self._writer_task = asyncio.create_task(self._background_writer())

    async def stop(self) -> None:
        """Stop the background writer task."""
        if self._writer_task is not None:
            self._writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer_task
            self._writer_task = None

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
        """Enqueue one execution record (non-blocking)."""
        def _normalize_non_empty(value: Any, field_name: str) -> str:
            if not isinstance(value, str):
                raise ValueError(f"{field_name} must be a non-empty string")
            normalized = value.strip()
            if not normalized:
                raise ValueError(f"{field_name} must be a non-empty string")
            return normalized

        normalized_tenant_id = _normalize_non_empty(tenant_id, "tenant_id")
        normalized_agent_id = _normalize_non_empty(agent_id, "agent_id")
        normalized_run_id = _normalize_non_empty(run_id, "run_id")
        normalized_capability_name = _normalize_non_empty(capability_name, "capability_name")
        normalized_task_type = _normalize_non_empty(task_type, "task_type")
        if not isinstance(input_params, dict):
            raise ValueError("input_params must be a dictionary")
        if output_result is not None and not isinstance(output_result, dict):
            raise ValueError("output_result must be a dictionary when provided")

        record = LedgerRecord(
            tenant_id=normalized_tenant_id,
            agent_id=normalized_agent_id,
            run_id=normalized_run_id,
            capability_name=normalized_capability_name,
            task_type=normalized_task_type,
            input_params=input_params,
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
        try:
            self._write_queue.put_nowait(record)
        except asyncio.QueueFull:
            # Drop oldest entry to prioritize recent records under overload.
            with contextlib.suppress(asyncio.QueueEmpty):
                _dropped = self._write_queue.get_nowait()
                self._write_queue.task_done()
            self._write_queue.put_nowait(record)
            logger.warning("Ledger queue full; dropped oldest record to apply backpressure")

    async def query_records(
        self,
        tenant_id: str,
        filters: LedgerQueryFilters,
    ) -> list[LedgerRecord]:
        """Query execution records with optional filters."""
        async with self._session_factory() as session:
            stmt = select(LedgerRecord).where(LedgerRecord.tenant_id == tenant_id)
            if filters.agent_id is not None:
                stmt = stmt.where(LedgerRecord.agent_id == filters.agent_id)
            if filters.capability_name is not None:
                stmt = stmt.where(
                    LedgerRecord.capability_name == filters.capability_name
                )
            if filters.status is not None:
                stmt = stmt.where(LedgerRecord.status == filters.status)
            if filters.execution_mode is not None:
                stmt = stmt.where(LedgerRecord.execution_mode == filters.execution_mode)
            if filters.start_date is not None:
                start_dt = datetime.combine(
                    filters.start_date, time.min, tzinfo=timezone.utc
                )
                stmt = stmt.where(LedgerRecord.created_at >= start_dt)
            if filters.end_date is not None:
                end_dt = datetime.combine(
                    filters.end_date, time.max, tzinfo=timezone.utc
                )
                stmt = stmt.where(LedgerRecord.created_at <= end_dt)
            if filters.order_by == "created_at DESC":
                stmt = stmt.order_by(LedgerRecord.created_at.desc())
            elif filters.order_by == "created_at ASC":
                stmt = stmt.order_by(LedgerRecord.created_at.asc())
            if filters.offset is not None:
                stmt = stmt.offset(filters.offset)
            if filters.limit is not None:
                stmt = stmt.limit(filters.limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_cost_summary(
        self,
        tenant_id: str,
        agent_id: str,
        start_date: date,
        end_date: date,
    ) -> CostSummary:
        """Sum estimated_cost for records in the date range."""
        from sqlalchemy import func as sql_func

        async with self._session_factory() as session:
            start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
            end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
            total_stmt = (
                select(sql_func.coalesce(sql_func.sum(LedgerRecord.estimated_cost), 0))
                .where(LedgerRecord.tenant_id == tenant_id)
                .where(LedgerRecord.agent_id == agent_id)
                .where(LedgerRecord.created_at >= start_dt)
                .where(LedgerRecord.created_at <= end_dt)
            )
            total_result = await session.execute(total_stmt)
            total = total_result.scalar_one()

            by_capability_stmt = (
                select(
                    LedgerRecord.capability_name,
                    sql_func.coalesce(sql_func.sum(LedgerRecord.estimated_cost), 0),
                )
                .where(LedgerRecord.tenant_id == tenant_id)
                .where(LedgerRecord.agent_id == agent_id)
                .where(LedgerRecord.created_at >= start_dt)
                .where(LedgerRecord.created_at <= end_dt)
                .group_by(LedgerRecord.capability_name)
            )
            by_capability_result = await session.execute(by_capability_stmt)
            by_capability = {
                str(capability): Decimal(str(cost)) if cost is not None else Decimal("0")
                for capability, cost in by_capability_result.all()
            }

            total_decimal = Decimal(str(total)) if total is not None else Decimal("0")
            return CostSummary(
                total_cost=total_decimal,
                by_agent={agent_id: total_decimal},
                by_capability=by_capability,
            )

    async def _background_writer(self) -> None:
        """Consume queue and flush batches to the database."""
        batch: list[LedgerRecord] = []
        while True:
            try:
                record = await asyncio.wait_for(
                    self._write_queue.get(),
                    timeout=self._flush_interval,
                )
                batch.append(record)
                # Drain immediately available queue items to improve throughput
                # under burst traffic while keeping timeout-based flush behavior.
                while len(batch) < self._batch_size and not self._write_queue.empty():
                    batch.append(self._write_queue.get_nowait())
                if len(batch) >= self._batch_size:
                    await self._flush_batch(batch)
                    batch = []
            except asyncio.TimeoutError:
                if batch:
                    await self._flush_batch(batch)
                    batch = []
            except asyncio.CancelledError:
                if batch:
                    await self._flush_batch(batch)
                raise
            except Exception as e:
                logger.exception("Ledger background writer error: %s", e)
                if batch:
                    await self._write_to_fallback_log(batch)
                    batch = []

    async def _flush_batch(self, batch: list[LedgerRecord]) -> None:
        """Write a batch of records to the database."""
        for attempt in range(1, self._flush_max_retries + 1):
            try:
                async with self._session_factory() as session:
                    session.add_all(batch)
                    await session.commit()
                logger.debug("Flushed %d ledger records", len(batch))
                return
            except Exception as e:
                if attempt >= self._flush_max_retries:
                    logger.exception("Failed to flush ledger batch after %d attempts: %s", attempt, e)
                    await self._write_to_fallback_log(batch)
                    return
                delay = self._flush_backoff_base_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Ledger batch flush failed (attempt %d/%d), retrying in %.2fs: %s",
                    attempt,
                    self._flush_max_retries,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)

    async def _write_to_fallback_log(self, batch: list[LedgerRecord]) -> None:
        """On DB failure, append records to a local fallback log."""
        for record in batch:
            line = json.dumps(
                {
                    "tenant_id": record.tenant_id,
                    "agent_id": record.agent_id,
                    "capability_name": record.capability_name,
                    "created_at": (
                        record.created_at.isoformat()
                        if getattr(record.created_at, "isoformat", None)
                        else str(record.created_at)
                    ),
                }
            ) + "\n"
            try:
                with open(self._fallback_log_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as e:
                logger.error("Failed to write fallback log: %s", e)
