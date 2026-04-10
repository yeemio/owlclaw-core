"""MCP tool bindings for durable task operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from owlclaw.capabilities.registry import CapabilityRegistry


class TaskClient(Protocol):
    """Minimal task client protocol used by MCP task tools."""

    async def run_task_now(self, task_name: str, **kwargs: Any) -> str:
        """Run one task immediately and return task id."""

    async def schedule_task(self, task_name: str, delay_seconds: int, **kwargs: Any) -> str:
        """Schedule one task and return task id."""

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        """Fetch one task status payload."""

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel one task by id."""


def register_task_mcp_tools(
    *,
    registry: CapabilityRegistry,
    task_client: TaskClient,
) -> None:
    """Register task management MCP tools into capability registry."""

    async def task_create(
        workflow_name: str,
        input_data: dict[str, Any] | None = None,
        schedule: int | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one durable task via Hatchet-backed task client."""
        task_name = _normalize_non_empty(workflow_name, "workflow_name")
        payload = _normalize_input_data(input_data)
        if schedule is None:
            task_id = await task_client.run_task_now(task_name, **payload)
            return {"task_id": task_id, "status": "running"}
        delay_seconds = _normalize_schedule(schedule)
        task_id = await task_client.schedule_task(task_name, delay_seconds=delay_seconds, **payload)
        return {"task_id": task_id, "status": "scheduled"}

    async def task_status(task_id: str) -> dict[str, Any]:
        """Get one task status."""
        normalized_task_id = _normalize_non_empty(task_id, "task_id")
        payload = await task_client.get_task_status(normalized_task_id)
        status = payload.get("status", "unknown")
        result: dict[str, Any] = {
            "task_id": str(payload.get("id", normalized_task_id)),
            "status": str(status),
        }
        if "result" in payload:
            result["result"] = payload["result"]
        if "error" in payload:
            result["error"] = payload["error"]
        return result

    async def task_cancel(task_id: str) -> dict[str, Any]:
        """Cancel one running/scheduled task."""
        normalized_task_id = _normalize_non_empty(task_id, "task_id")
        cancelled = await task_client.cancel_task(normalized_task_id)
        return {"task_id": normalized_task_id, "cancelled": cancelled}

    registry.register_handler("task_create", task_create)
    registry.register_handler("task_status", task_status)
    registry.register_handler("task_cancel", task_cancel)


def _normalize_non_empty(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _normalize_input_data(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("input_data must be an object")
    return value


def _normalize_schedule(value: int | Mapping[str, Any]) -> int:
    if isinstance(value, bool):
        raise ValueError("schedule must be integer seconds or {delay_seconds}")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("schedule delay_seconds must be > 0")
        return value
    if isinstance(value, Mapping):
        raw = value.get("delay_seconds")
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError("schedule.delay_seconds must be an integer")
        if raw <= 0:
            raise ValueError("schedule.delay_seconds must be > 0")
        return raw
    raise ValueError("schedule must be integer seconds or {delay_seconds}")
