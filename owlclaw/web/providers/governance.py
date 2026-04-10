"""Governance provider implementation for console backend."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timezone
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select

from owlclaw.db import get_engine
from owlclaw.db.session import create_session_factory
from owlclaw.governance.ledger import LedgerRecord
from owlclaw.governance.visibility import CapabilityView, RunContext, VisibilityFilter

logger = logging.getLogger(__name__)

_VALID_GRANULARITY: set[str] = {"day", "week", "month"}


class DefaultGovernanceProvider:
    """Aggregate governance metrics for Console APIs."""

    def __init__(
        self,
        *,
        visibility_filter: VisibilityFilter | None = None,
        capability_loader: Callable[[], list[CapabilityView]] | None = None,
    ) -> None:
        self._visibility_filter = visibility_filter
        self._capability_loader = capability_loader

    async def get_budget_trend(
        self,
        tenant_id: str,
        start_date: date,
        end_date: date,
        granularity: str,
    ) -> list[dict[str, Any]]:
        normalized = granularity.strip().lower()
        if normalized not in _VALID_GRANULARITY:
            raise ValueError("granularity must be one of: day, week, month")

        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)

        try:
            engine = get_engine()
            session_factory = create_session_factory(engine)
            async with session_factory() as session:
                period_expr = func.date_trunc(normalized, LedgerRecord.created_at)
                statement = (
                    select(
                        period_expr.label("period"),
                        func.coalesce(func.sum(LedgerRecord.estimated_cost), 0).label("total_cost"),
                        func.count(LedgerRecord.id).label("executions"),
                    )
                    .where(LedgerRecord.tenant_id == tenant_id)
                    .where(LedgerRecord.created_at >= start_dt)
                    .where(LedgerRecord.created_at <= end_dt)
                    .group_by(period_expr)
                    .order_by(period_expr.asc())
                )
                rows = (await session.execute(statement)).all()
        except Exception:
            logger.exception("Failed to query governance budget trend.")
            return []

        result: list[dict[str, Any]] = []
        for period, total_cost, executions in rows:
            result.append(
                {
                    "period_start": period.isoformat() if hasattr(period, "isoformat") else str(period),
                    "granularity": normalized,
                    "total_cost": str(Decimal(str(total_cost))),
                    "executions": int(executions or 0),
                }
            )
        return result

    async def get_circuit_breaker_states(self, tenant_id: str) -> list[dict[str, Any]]:
        """Return per-capability circuit-like status based on recent failure rate."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        failure_case = case((func.lower(LedgerRecord.status) == "failure", 1), else_=0)

        try:
            engine = get_engine()
            session_factory = create_session_factory(engine)
            async with session_factory() as session:
                statement = (
                    select(
                        LedgerRecord.capability_name,
                        func.count(LedgerRecord.id).label("attempts"),
                        func.coalesce(func.sum(failure_case), 0).label("failures"),
                        func.max(LedgerRecord.created_at).label("last_seen"),
                    )
                    .where(LedgerRecord.tenant_id == tenant_id)
                    .where(LedgerRecord.created_at >= cutoff)
                    .group_by(LedgerRecord.capability_name)
                    .order_by(LedgerRecord.capability_name.asc())
                )
                rows = (await session.execute(statement)).all()
        except Exception:
            logger.exception("Failed to query governance circuit breaker states.")
            return []

        states: list[dict[str, Any]] = []
        for capability_name, attempts_raw, failures_raw, last_seen in rows:
            attempts = int(attempts_raw or 0)
            failures = int(failures_raw or 0)
            failure_rate = (failures / attempts) if attempts > 0 else 0.0
            if attempts >= 5 and failure_rate >= 0.5:
                state = "open"
            elif attempts >= 2 and failure_rate >= 0.25:
                state = "half_open"
            else:
                state = "closed"
            states.append(
                {
                    "capability_name": capability_name,
                    "state": state,
                    "attempts_24h": attempts,
                    "failures_24h": failures,
                    "failure_rate_24h": round(failure_rate, 4),
                    "last_seen_at": (
                        last_seen.isoformat() if hasattr(last_seen, "isoformat") else None
                    ),
                }
            )

        return states

    async def get_visibility_matrix(
        self,
        tenant_id: str,
        agent_id: str | None,
    ) -> dict[str, Any]:
        if self._visibility_filter is not None and self._capability_loader is not None:
            matrix = await self._build_matrix_from_visibility_filter(
                tenant_id=tenant_id,
                agent_id=agent_id or "console",
            )
            return {
                "agent_id": agent_id,
                "source": "visibility_filter",
                "items": matrix,
            }

        return await self._build_matrix_from_ledger(tenant_id=tenant_id, agent_id=agent_id)

    async def _build_matrix_from_visibility_filter(
        self,
        *,
        tenant_id: str,
        agent_id: str,
    ) -> list[dict[str, Any]]:
        if self._capability_loader is None or self._visibility_filter is None:
            return []

        capabilities = list(self._capability_loader())
        visible = await self._visibility_filter.filter_capabilities(
            capabilities=capabilities,
            agent_id=agent_id,
            context=RunContext(tenant_id=tenant_id),
        )
        visible_names = {item.name for item in visible}
        return [
            {
                "agent_id": agent_id,
                "capability_name": cap.name,
                "visible": cap.name in visible_names,
                "reason": "allowed" if cap.name in visible_names else "filtered_by_constraints",
            }
            for cap in capabilities
        ]

    async def _build_matrix_from_ledger(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
    ) -> dict[str, Any]:
        try:
            engine = get_engine()
            session_factory = create_session_factory(engine)
            async with session_factory() as session:
                statement = (
                    select(
                        LedgerRecord.agent_id,
                        LedgerRecord.capability_name,
                        func.count(LedgerRecord.id).label("executions"),
                    )
                    .where(LedgerRecord.tenant_id == tenant_id)
                    .group_by(LedgerRecord.agent_id, LedgerRecord.capability_name)
                    .order_by(LedgerRecord.agent_id.asc(), LedgerRecord.capability_name.asc())
                )
                if agent_id:
                    statement = statement.where(LedgerRecord.agent_id == agent_id)
                rows = (await session.execute(statement)).all()
        except Exception:
            logger.exception("Failed to build governance visibility matrix from ledger.")
            return {"agent_id": agent_id, "source": "ledger", "items": []}

        items = [
            {
                "agent_id": row_agent,
                "capability_name": capability_name,
                "visible": True,
                "reason": "observed_execution",
                "executions": int(executions or 0),
            }
            for row_agent, capability_name, executions in rows
        ]
        return {"agent_id": agent_id, "source": "ledger", "items": items}
