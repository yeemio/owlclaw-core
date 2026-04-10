"""Minimal demo for OwlClaw built-in tools.

Scenarios covered:
1) Agent self-scheduling (schedule_once)
2) Agent remembers and recalls lessons (remember/recall)
3) Agent queries state then logs no_action decision (query_state/log_decision)
4) Agent records decision reasoning (log_decision)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from owlclaw.agent import BuiltInTools, BuiltInToolsContext


class DemoRegistry:
    """Simple registry that serves one state provider."""

    async def get_state(self, state_name: str) -> dict[str, Any]:
        if state_name == "market_state":
            return {"is_trading_time": False, "phase": "post_close"}
        raise ValueError(f"No state provider registered for '{state_name}'")


class DemoHatchet:
    """In-memory hatchet stub for scheduling demonstrations."""

    async def schedule_task(self, _task_name: str, _delay_seconds: int, **_kwargs: Any) -> str:
        return "schedule-once-001"

    async def schedule_cron(
        self,
        *,
        workflow_name: str,
        cron_name: str,
        expression: str,
        input_data: dict[str, Any],
    ) -> str:
        _ = (workflow_name, cron_name, expression, input_data)
        return "schedule-cron-001"

    async def cancel_task(self, _schedule_id: str) -> bool:
        return True


@dataclass
class _MemoryItem:
    memory_id: str
    content: str
    tags: list[str]
    created_at: str


class DemoMemory:
    """In-memory memory backend for remember/recall demo."""

    def __init__(self) -> None:
        self._items: list[_MemoryItem] = []
        self._seq = 0

    async def write(self, *, content: str, tags: list[str]) -> dict[str, str]:
        self._seq += 1
        memory_id = f"mem-{self._seq:03d}"
        created_at = "2026-02-23T00:00:00Z"
        self._items.append(_MemoryItem(memory_id=memory_id, content=content, tags=tags, created_at=created_at))
        return {"memory_id": memory_id, "created_at": created_at}

    async def search(self, *, query: str, limit: int, tags: list[str]) -> list[dict[str, Any]]:
        query_lower = query.lower()
        required_tags = set(tags)
        matched: list[dict[str, Any]] = []
        for item in self._items:
            if query_lower not in item.content.lower():
                continue
            if required_tags and not required_tags.issubset(set(item.tags)):
                continue
            matched.append(
                {
                    "memory_id": item.memory_id,
                    "content": item.content,
                    "tags": item.tags,
                    "created_at": item.created_at,
                    "score": 0.95,
                }
            )
        return matched[:limit]


class DemoLedger:
    """No-op ledger stub to satisfy BuiltInTools dependencies."""

    async def record_execution(self, **_kwargs: Any) -> None:
        _ = Decimal("0")


async def main() -> None:
    tools = BuiltInTools(
        capability_registry=DemoRegistry(),
        ledger=DemoLedger(),
        hatchet_client=DemoHatchet(),
        memory_system=DemoMemory(),
    )
    ctx = BuiltInToolsContext(agent_id="demo-agent", run_id="run-001", tenant_id="default")

    # 1) Self-schedule
    schedule_result = await tools.execute(
        "schedule_once",
        {"delay_seconds": 300, "focus": "check entry opportunities"},
        ctx,
    )
    print("schedule_once:", schedule_result)

    # 2) Remember and recall
    remember_result = await tools.execute(
        "remember",
        {"content": "After a sharp drop, rebound signals can be accurate within 2 hours.", "tags": ["trading"]},
        ctx,
    )
    recall_result = await tools.execute(
        "recall",
        {"query": "sharp drop", "limit": 5, "tags": ["trading"]},
        ctx,
    )
    print("remember:", remember_result)
    print("recall:", recall_result)

    # 3) Query state and decide no_action
    state_result = await tools.execute("query_state", {"state_name": "market_state"}, ctx)
    if state_result.get("state", {}).get("is_trading_time") is False:
        decision_result = await tools.execute(
            "log_decision",
            {"reasoning": "Non-trading hours, skipping checks.", "decision_type": "no_action"},
            ctx,
        )
        print("query_state:", state_result)
        print("log_decision(no_action):", decision_result)

    # 4) Explicit decision log
    explicit_log_result = await tools.execute(
        "log_decision",
        {"reasoning": "Scheduled a delayed follow-up check.", "decision_type": "schedule_decision"},
        ctx,
    )
    print("log_decision(schedule_decision):", explicit_log_result)


if __name__ == "__main__":
    asyncio.run(main())
