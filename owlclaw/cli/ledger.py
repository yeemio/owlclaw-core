"""Ledger CLI command helpers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import typer

from owlclaw.db import create_engine, create_session_factory, get_engine
from owlclaw.db.exceptions import ConfigurationError
from owlclaw.governance.ledger import Ledger, LedgerQueryFilters


def _normalize_text(value: str) -> str:
    return value.strip()


def _serialize_record(record: Any) -> dict[str, Any]:
    return {
        "tenant_id": record.tenant_id,
        "agent_id": record.agent_id,
        "run_id": record.run_id,
        "capability_name": record.capability_name,
        "task_type": record.task_type,
        "status": record.status,
        "estimated_cost": str(record.estimated_cost) if isinstance(record.estimated_cost, Decimal) else record.estimated_cost,
        "execution_time_ms": record.execution_time_ms,
        "llm_model": record.llm_model,
        "created_at": (
            record.created_at.isoformat() if isinstance(record.created_at, datetime) else str(record.created_at)
        ),
        "error_message": record.error_message,
    }


async def _query_impl(
    *,
    tenant: str,
    agent_id: str,
    caller: str,
    caller_prefix: str,
    status: str,
    limit: int,
    order_desc: bool,
    database_url: str,
) -> list[dict[str, Any]]:
    engine = create_engine(database_url) if database_url else get_engine()
    session_factory = create_session_factory(engine)
    ledger = Ledger(session_factory=session_factory)
    filters = LedgerQueryFilters(
        agent_id=agent_id or None,
        capability_name=caller or None,
        status=status or None,
        limit=limit,
        order_by="created_at DESC" if order_desc else "created_at ASC",
    )
    records = await ledger.query_records(tenant_id=tenant, filters=filters)
    if caller_prefix:
        records = [row for row in records if row.capability_name.startswith(caller_prefix)]
    return [_serialize_record(row) for row in records]


def query_command(
    *,
    tenant: str = "default",
    agent_id: str = "",
    caller: str = "",
    caller_prefix: str = "",
    status: str = "",
    limit: int = 20,
    order_desc: bool = True,
    database_url: str = "",
) -> None:
    normalized_tenant = _normalize_text(tenant)
    if not normalized_tenant:
        raise typer.BadParameter("tenant must not be empty.")
    if limit < 1:
        raise typer.BadParameter("limit must be >= 1.")

    try:
        rows = asyncio.run(
            _query_impl(
                tenant=normalized_tenant,
                agent_id=_normalize_text(agent_id),
                caller=_normalize_text(caller),
                caller_prefix=_normalize_text(caller_prefix),
                status=_normalize_text(status),
                limit=limit,
                order_desc=order_desc,
                database_url=_normalize_text(database_url),
            )
        )
    except ConfigurationError:
        typer.echo("Database not configured. Set OWLCLAW_DATABASE_URL to query persisted ledger records.")
        return
    typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
