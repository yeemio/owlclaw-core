"""Hatchet bridge for running AgentRuntime as Hatchet tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from owlclaw.agent.runtime.runtime import AgentRuntime


class HatchetRuntimeBridge:
    """Register and execute AgentRuntime through Hatchet task APIs."""

    def __init__(
        self,
        runtime: AgentRuntime,
        hatchet_client: Any,
        *,
        task_name: str = "agent_run",
        retries: int = 3,
        max_concurrency: int = 1,
        default_tenant_id: str = "default",
    ) -> None:
        if not isinstance(task_name, str) or not task_name.strip():
            raise ValueError("task_name must be a non-empty string")
        if not isinstance(retries, int) or retries < 0:
            raise ValueError("retries must be a non-negative integer")
        if not isinstance(max_concurrency, int) or max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer")
        if not isinstance(default_tenant_id, str) or not default_tenant_id.strip():
            raise ValueError("default_tenant_id must be a non-empty string")
        self.runtime = runtime
        self.hatchet = hatchet_client
        self.task_name = task_name.strip()
        self.retries = retries
        self.max_concurrency = max_concurrency
        self.default_tenant_id = default_tenant_id.strip()
        self._lock = asyncio.Semaphore(max_concurrency)
        self._registered = False
        self._task_handler: Callable[..., Any] | None = None

    @staticmethod
    def _normalize_input(input_payload: dict[str, Any] | None) -> dict[str, Any]:
        if input_payload is None:
            return {}
        if not isinstance(input_payload, dict):
            raise ValueError("Hatchet input must be a dictionary")
        return dict(input_payload)

    async def run_payload(self, input_payload: dict[str, Any] | None) -> dict[str, Any]:
        """Run AgentRuntime from a Hatchet task input payload."""
        payload = self._normalize_input(input_payload)
        event_name = payload.get("event_name") or payload.get("trigger") or "hatchet_task"
        focus = payload.get("focus")
        trigger_payload = payload.get("payload")
        tenant_id = payload.get("tenant_id") or self.default_tenant_id
        if not isinstance(trigger_payload, dict):
            trigger_payload = {}
        async with self._lock:
            return await self.runtime.trigger_event(
                str(event_name),
                focus=focus if isinstance(focus, str) else None,
                payload=trigger_payload,
                tenant_id=str(tenant_id),
            )

    def register_task(self) -> Callable[..., Any]:
        """Register AgentRuntime as a Hatchet task and return task handler."""
        if self._registered and self._task_handler is not None:
            return self._task_handler
        decorator = self.hatchet.task(
            name=self.task_name,
            retries=self.retries,
        )

        async def _handler(input: dict[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:  # noqa: A002
            del ctx
            return await self.run_payload(input)

        self._task_handler = decorator(_handler)
        self._registered = True
        return self._task_handler

    async def run_now(self, **kwargs: Any) -> str:
        """Trigger an immediate Hatchet run for this runtime task."""
        if not hasattr(self.hatchet, "run_task_now"):
            raise RuntimeError("hatchet client does not support run_task_now")
        run_id = await self.hatchet.run_task_now(self.task_name, **kwargs)
        return str(run_id)

    async def schedule_task(self, delay_seconds: int, **kwargs: Any) -> str:
        """Schedule a runtime run after delay_seconds."""
        if not hasattr(self.hatchet, "schedule_task"):
            raise RuntimeError("hatchet client does not support schedule_task")
        run_id = await self.hatchet.schedule_task(self.task_name, delay_seconds, **kwargs)
        return str(run_id)

    async def schedule_cron(
        self,
        cron_name: str,
        expression: str,
        input_data: dict[str, Any],
    ) -> str:
        """Create a Hatchet cron trigger for this runtime task."""
        if not hasattr(self.hatchet, "schedule_cron"):
            raise RuntimeError("hatchet client does not support schedule_cron")
        run_id = await self.hatchet.schedule_cron(
            workflow_name=self.task_name,
            cron_name=cron_name,
            expression=expression,
            input_data=input_data,
        )
        return str(run_id)

    async def send_signal(self, run_id: str, signal_name: str, payload: dict[str, Any] | None = None) -> Any:
        """Send a signal to a running Hatchet task if client supports it."""
        signal_fn = getattr(self.hatchet, "send_signal", None)
        if not callable(signal_fn):
            raise RuntimeError("hatchet client does not support send_signal")
        return await signal_fn(run_id=run_id, signal_name=signal_name, payload=payload or {})
