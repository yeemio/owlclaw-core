"""Triggers provider implementation for console backend."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from owlclaw.db import get_engine
from owlclaw.db.exceptions import ConfigurationError
from owlclaw.db.session import create_session_factory
from owlclaw.governance.ledger import LedgerRecord

TRIGGER_TYPES: tuple[str, ...] = ("cron", "webhook", "queue", "db_change", "api", "signal")


class DefaultTriggersProvider:
    """Aggregate trigger states and execution history from ledger data."""

    async def list_triggers(self, tenant_id: str) -> list[dict[str, Any]]:
        try:
            records = await self._load_trigger_records(tenant_id=tenant_id, lookback_hours=24, limit=1000)
        except ConfigurationError:
            return []
        grouped: dict[tuple[str, str], list[LedgerRecord]] = {}
        for record in records:
            trigger_type = self._infer_trigger_type(record)
            if trigger_type is None:
                continue
            trigger_id = self._infer_trigger_id(record, trigger_type=trigger_type)
            grouped.setdefault((trigger_type, trigger_id), []).append(record)

        items: list[dict[str, Any]] = []
        covered_types: set[str] = set()
        for (trigger_type, trigger_id), group in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
            covered_types.add(trigger_type)
            success_count = sum(1 for row in group if str(row.status).lower() == "success")
            total = len(group)
            success_rate = (success_count / total) if total > 0 else 0.0
            latest = max(group, key=lambda row: row.created_at)
            next_run = None
            if isinstance(latest.input_params, dict):
                candidate = latest.input_params.get("next_run")
                if isinstance(candidate, str) and candidate.strip():
                    next_run = candidate.strip()
            items.append(
                {
                    "id": trigger_id,
                    "name": trigger_id,
                    "type": trigger_type,
                    "enabled": True,
                    "next_run": next_run,
                    "success_rate": round(success_rate, 4),
                    "executions_24h": total,
                    "last_run_at": latest.created_at.isoformat() if hasattr(latest.created_at, "isoformat") else None,
                }
            )

        for trigger_type in TRIGGER_TYPES:
            if trigger_type in covered_types:
                continue
            items.append(
                {
                    "id": trigger_type,
                    "name": trigger_type,
                    "type": trigger_type,
                    "enabled": False,
                    "next_run": None,
                    "success_rate": 0.0,
                    "executions_24h": 0,
                    "last_run_at": None,
                }
            )
        return items

    async def get_trigger_history(
        self,
        trigger_id: str,
        tenant_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        try:
            records = await self._load_trigger_records(tenant_id=tenant_id, lookback_hours=24 * 30, limit=5000)
        except ConfigurationError:
            return [], 0
        matched: list[LedgerRecord] = []
        for record in records:
            trigger_type = self._infer_trigger_type(record)
            if trigger_type is None:
                continue
            inferred_id = self._infer_trigger_id(record, trigger_type=trigger_type)
            if inferred_id == trigger_id:
                matched.append(record)

        matched.sort(key=lambda row: row.created_at, reverse=True)
        total = len(matched)
        page = matched[offset : offset + limit]
        items = [
            {
                "id": str(row.id),
                "run_id": row.run_id,
                "trigger_id": self._infer_trigger_id(row, trigger_type=self._infer_trigger_type(row) or "unknown"),
                "trigger_type": self._infer_trigger_type(row) or "unknown",
                "status": row.status,
                "execution_time_ms": row.execution_time_ms,
                "error_message": row.error_message,
                "created_at": row.created_at.isoformat() if hasattr(row.created_at, "isoformat") else None,
            }
            for row in page
        ]
        return items, total

    async def _load_trigger_records(
        self,
        *,
        tenant_id: str,
        lookback_hours: int,
        limit: int,
    ) -> list[LedgerRecord]:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=lookback_hours)
        engine = get_engine()
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            statement = (
                select(LedgerRecord)
                .where(LedgerRecord.tenant_id == tenant_id)
                .where(LedgerRecord.created_at >= cutoff)
                .order_by(LedgerRecord.created_at.desc())
                .limit(limit)
            )
            rows = (await session.execute(statement)).scalars().all()
        return list(rows)

    def _infer_trigger_id(self, record: LedgerRecord, *, trigger_type: str) -> str:
        if isinstance(record.input_params, dict):
            explicit = record.input_params.get("trigger_id")
            if isinstance(explicit, str) and explicit.strip():
                return explicit.strip()
            event_name = record.input_params.get("event_name")
            if isinstance(event_name, str) and event_name.strip():
                return event_name.strip()
        if isinstance(record.capability_name, str) and record.capability_name.strip():
            return record.capability_name.strip()
        return trigger_type

    def _infer_trigger_type(self, record: LedgerRecord) -> str | None:
        if isinstance(record.input_params, dict):
            explicit = record.input_params.get("trigger_type")
            if isinstance(explicit, str):
                normalized = explicit.strip().lower()
                if normalized in TRIGGER_TYPES:
                    return normalized
                if normalized == "manual":
                    return "signal"

        task_type = str(record.task_type).strip().lower()
        if task_type.startswith("cron"):
            return "cron"
        if "queue" in task_type:
            return "queue"
        if task_type == "signal":
            return "signal"
        if task_type == "trigger":
            if isinstance(record.input_params, dict):
                explicit = record.input_params.get("trigger_type")
                if isinstance(explicit, str):
                    normalized = explicit.strip().lower()
                    if normalized in TRIGGER_TYPES:
                        return normalized
            return "api"
        return None
