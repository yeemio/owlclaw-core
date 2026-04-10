"""Agents provider implementation for console backend."""

from __future__ import annotations

from typing import Any

from sqlalchemy import distinct, func, select

from owlclaw.db import get_engine
from owlclaw.db.session import create_session_factory
from owlclaw.governance.ledger import LedgerRecord


class DefaultAgentsProvider:
    """Aggregate agent list/detail data from ledger records."""

    async def list_agents(self, tenant_id: str) -> list[dict[str, Any]]:
        engine = get_engine()
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            statement = (
                select(
                    LedgerRecord.agent_id,
                    func.count(LedgerRecord.id).label("runs"),
                    func.max(LedgerRecord.created_at).label("last_run_at"),
                    func.count(distinct(LedgerRecord.capability_name)).label("capability_count"),
                )
                .where(LedgerRecord.tenant_id == tenant_id)
                .group_by(LedgerRecord.agent_id)
                .order_by(LedgerRecord.agent_id.asc())
            )
            rows = (await session.execute(statement)).all()

        return [
            {
                "id": agent_id,
                "agent_id": agent_id,
                "identity_summary": f"Agent {agent_id}",
                "memory_stats": {"short_term": 0, "long_term": 0},
                "knowledge_mounts": [],
                "run_count": int(runs or 0),
                "capability_count": int(capability_count or 0),
                "last_run_at": last_run_at.isoformat() if hasattr(last_run_at, "isoformat") else None,
            }
            for agent_id, runs, last_run_at, capability_count in rows
        ]

    async def get_agent_detail(self, agent_id: str, tenant_id: str) -> dict[str, Any] | None:
        engine = get_engine()
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            rows_statement = (
                select(LedgerRecord)
                .where(LedgerRecord.tenant_id == tenant_id)
                .where(LedgerRecord.agent_id == agent_id)
                .order_by(LedgerRecord.created_at.desc())
                .limit(20)
            )
            rows = (await session.execute(rows_statement)).scalars().all()
        if not rows:
            return None

        history = [
            {
                "id": str(row.id),
                "run_id": row.run_id,
                "capability_name": row.capability_name,
                "status": row.status,
                "execution_time_ms": row.execution_time_ms,
                "estimated_cost": str(row.estimated_cost),
                "created_at": row.created_at.isoformat() if hasattr(row.created_at, "isoformat") else None,
            }
            for row in rows
        ]
        return {
            "id": agent_id,
            "agent_id": agent_id,
            "identity_summary": f"Agent {agent_id}",
            "memory_stats": {"short_term": 0, "long_term": 0},
            "knowledge_mounts": [],
            "recent_history": history,
        }
