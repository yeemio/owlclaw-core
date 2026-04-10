"""Ledger provider implementation for console backend."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from owlclaw.db import get_engine
from owlclaw.db.exceptions import ConfigurationError
from owlclaw.db.session import create_session_factory
from owlclaw.governance.ledger import LedgerRecord

logger = logging.getLogger(__name__)


class DefaultLedgerProvider:
    """Query and serialize ledger audit records for Console API."""

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
        try:
            engine = get_engine()
            session_factory = create_session_factory(engine)
            async with session_factory() as session:
                where_conditions = self._build_filters(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    capability_name=capability_name,
                    status=status,
                    start_date=start_date,
                    end_date=end_date,
                    min_cost=min_cost,
                    max_cost=max_cost,
                )
                total_stmt = select(func.count(LedgerRecord.id)).where(*where_conditions)
                total = int((await session.execute(total_stmt)).scalar_one() or 0)

                list_stmt = select(LedgerRecord).where(*where_conditions)
                list_stmt = self._apply_order(list_stmt, order_by=order_by)
                list_stmt = list_stmt.offset(offset).limit(limit)
                rows = (await session.execute(list_stmt)).scalars().all()
        except ConfigurationError:
            return [], 0
        except Exception:
            logger.exception("Failed to query ledger records for console API.")
            return [], 0

        return [self._serialize_summary(record) for record in rows], total

    async def get_record_detail(self, record_id: str, tenant_id: str) -> dict[str, Any] | None:
        try:
            parsed_id = uuid.UUID(record_id)
        except ValueError:
            return None

        try:
            engine = get_engine()
            session_factory = create_session_factory(engine)
            async with session_factory() as session:
                statement = (
                    select(LedgerRecord)
                    .where(LedgerRecord.id == parsed_id)
                    .where(LedgerRecord.tenant_id == tenant_id)
                    .limit(1)
                )
                record = (await session.execute(statement)).scalar_one_or_none()
        except ConfigurationError:
            return None
        except Exception:
            logger.exception("Failed to query ledger record detail for console API.")
            return None

        if record is None:
            return None
        return self._serialize_detail(record)

    def _build_filters(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
        capability_name: str | None,
        status: str | None,
        start_date: date | None,
        end_date: date | None,
        min_cost: Decimal | None,
        max_cost: Decimal | None,
    ) -> list[Any]:
        conditions: list[Any] = [LedgerRecord.tenant_id == tenant_id]
        if agent_id:
            conditions.append(LedgerRecord.agent_id == agent_id)
        if capability_name:
            conditions.append(LedgerRecord.capability_name == capability_name)
        if status:
            conditions.append(LedgerRecord.status == status)
        if start_date is not None:
            start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
            conditions.append(LedgerRecord.created_at >= start_dt)
        if end_date is not None:
            end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
            conditions.append(LedgerRecord.created_at <= end_dt)
        if min_cost is not None:
            conditions.append(LedgerRecord.estimated_cost >= min_cost)
        if max_cost is not None:
            conditions.append(LedgerRecord.estimated_cost <= max_cost)
        return conditions

    def _apply_order(self, statement: Any, *, order_by: str | None) -> Any:
        normalized = (order_by or "created_at_desc").strip().lower()
        if normalized == "created_at_asc":
            return statement.order_by(LedgerRecord.created_at.asc())
        if normalized == "cost_desc":
            return statement.order_by(LedgerRecord.estimated_cost.desc(), LedgerRecord.created_at.desc())
        if normalized == "cost_asc":
            return statement.order_by(LedgerRecord.estimated_cost.asc(), LedgerRecord.created_at.desc())
        return statement.order_by(LedgerRecord.created_at.desc())

    def _serialize_summary(self, record: LedgerRecord) -> dict[str, Any]:
        return {
            "id": str(record.id),
            "agent_id": record.agent_id,
            "capability_name": record.capability_name,
            "task_type": record.task_type,
            "status": record.status,
            "execution_time_ms": record.execution_time_ms,
            "estimated_cost": str(record.estimated_cost),
            "llm_model": record.llm_model,
            "created_at": record.created_at.isoformat() if hasattr(record.created_at, "isoformat") else None,
        }

    def _serialize_detail(self, record: LedgerRecord) -> dict[str, Any]:
        base = self._serialize_summary(record)
        base.update(
            {
                "tenant_id": record.tenant_id,
                "run_id": record.run_id,
                "input_params": record.input_params,
                "output_result": record.output_result,
                "decision_reasoning": record.decision_reasoning,
                "llm_tokens_input": record.llm_tokens_input,
                "llm_tokens_output": record.llm_tokens_output,
                "error_message": record.error_message,
                "migration_weight": record.migration_weight,
                "execution_mode": record.execution_mode,
                "risk_level": str(record.risk_level) if record.risk_level is not None else None,
                "approval_by": record.approval_by,
                "approval_time": record.approval_time.isoformat() if record.approval_time else None,
            }
        )
        return base
