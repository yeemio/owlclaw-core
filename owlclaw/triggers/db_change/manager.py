"""DB change trigger manager."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
from typing import Any, Protocol

from owlclaw.triggers.db_change.adapter import DBChangeAdapter, DBChangeEvent
from owlclaw.triggers.db_change.aggregator import EventAggregator
from owlclaw.triggers.db_change.config import DBChangeTriggerConfig

logger = logging.getLogger(__name__)
_SENSITIVE_KEYS = ("password", "passwd", "pwd", "token", "api_key", "apikey", "secret", "authorization")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|authorization)\b\s*([:=])\s*([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+\S+")
_URL_CRED_PATTERN = re.compile(r"(?i)\b([a-z][a-z0-9+\-.]*://)([^:/\s]+):([^@/\s]+)@")


def _redact_sensitive_text(text: str) -> str:
    redacted = _KEY_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    redacted = _BEARER_PATTERN.sub("Bearer ***", redacted)
    redacted = _URL_CRED_PATTERN.sub(r"\1***:***@", redacted)
    return redacted


def _redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if any(marker in key.lower() for marker in _SENSITIVE_KEYS):
                redacted[key] = "***"
                continue
            redacted[key] = _redact_sensitive_data(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive_data(item) for item in value)
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


class GovernanceServiceProtocol(Protocol):
    async def allow_trigger(self, event_name: str, tenant_id: str) -> bool: ...


class AgentRuntimeProtocol(Protocol):
    async def trigger_event(
        self,
        event_name: str,
        payload: dict[str, Any],
        focus: str | None = None,
        tenant_id: str = "default",
    ) -> Any: ...


class LedgerProtocol(Protocol):
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
    ) -> None: ...


@dataclass(slots=True)
class _TriggerState:
    config: DBChangeTriggerConfig
    aggregator: EventAggregator


class DBChangeTriggerManager:
    """Manage db change trigger registrations and dispatch flow."""

    def __init__(
        self,
        *,
        adapter: DBChangeAdapter,
        governance: GovernanceServiceProtocol,
        agent_runtime: AgentRuntimeProtocol,
        ledger: LedgerProtocol | None = None,
        retry_interval_seconds: float = 5.0,
        max_retry_attempts: int = 5,
        local_queue_max_size: int = 1000,
    ) -> None:
        self._adapter = adapter
        self._governance = governance
        self._agent_runtime = agent_runtime
        self._ledger = ledger
        self._states: dict[str, _TriggerState] = {}
        self._lock = asyncio.Lock()
        self._started = False
        self._handlers: dict[str, Callable[[list[DBChangeEvent]], Awaitable[None]] | None] = {}
        self._retry_interval_seconds = retry_interval_seconds
        self._max_retry_attempts = max(0, int(max_retry_attempts))
        self._local_queue_max_size = local_queue_max_size
        self._local_retry_queue: asyncio.Queue[tuple[DBChangeTriggerConfig, dict[str, Any], int]] = asyncio.Queue(
            maxsize=local_queue_max_size
        )
        self._dlq_events: list[dict[str, Any]] = []
        self._retry_task: asyncio.Task[None] | None = None
        self._adapter.on_event(self._on_event)

    def register(
        self,
        config: DBChangeTriggerConfig,
        handler: Callable[[list[DBChangeEvent]], Awaitable[None]] | None = None,
    ) -> None:
        mode = "hybrid" if config.batch_size and config.debounce_seconds else "batch" if config.batch_size else "debounce" if config.debounce_seconds else "passthrough"
        aggregator = EventAggregator(
            mode=mode,  # type: ignore[arg-type]
            debounce_seconds=config.debounce_seconds,
            batch_size=config.batch_size,
            max_buffer_events=config.max_buffer_events,
            on_flush=lambda events: self._on_aggregated(config, events),
        )
        self._states[config.channel] = _TriggerState(config=config, aggregator=aggregator)
        self._handlers[config.channel] = handler

    @property
    def registered_channels_count(self) -> int:
        """Expose registered channel count without leaking internal state mapping."""
        return len(self._states)

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            channels = list(self._states.keys())
            await self._adapter.start(channels)
            self._started = True
            self._retry_task = asyncio.create_task(self._retry_loop())

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            if self._retry_task is not None:
                self._retry_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._retry_task
                self._retry_task = None
            await self._adapter.stop()
            self._started = False

    async def _on_event(self, event: DBChangeEvent) -> None:
        state = self._states.get(event.channel)
        if state is None:
            return
        if self._is_payload_oversized(event, state.config.max_payload_bytes):
            logger.warning("db_change payload over size limit on channel %s", event.channel)
            return
        await state.aggregator.push(event)

    async def _on_aggregated(self, config: DBChangeTriggerConfig, events: list[DBChangeEvent]) -> None:
        allowed = await self._governance.allow_trigger(config.event_name, config.tenant_id)
        if not allowed:
            if self._ledger is not None:
                await self._ledger.record_execution(
                    tenant_id=config.tenant_id,
                    agent_id=config.agent_id,
                    run_id="db-change-blocked",
                    capability_name="db_change_trigger",
                    task_type="trigger",
                    input_params={"channel": config.channel, "event_count": len(events)},
                    output_result=None,
                    decision_reasoning="governance_blocked",
                    execution_time_ms=0,
                    llm_model="",
                    llm_tokens_input=0,
                    llm_tokens_output=0,
                    estimated_cost=Decimal("0"),
                    status="blocked",
                    error_message=None,
                )
            return
        payload = {"channel": config.channel, "events": [event.payload for event in events], "event_count": len(events)}
        if not await self._dispatch_agent_trigger(config, payload):
            return
        fallback = self._handlers.get(config.channel)
        if fallback is not None:
            await fallback(events)

    async def _dispatch_agent_trigger(self, config: DBChangeTriggerConfig, payload: dict[str, Any]) -> bool:
        try:
            await self._agent_runtime.trigger_event(
                event_name=config.event_name,
                payload=payload,
                focus=config.focus,
                tenant_id=config.tenant_id,
            )
            return True
        except Exception as exc:
            logger.warning("db_change trigger dispatch failed, queued locally: %s", _redact_sensitive_text(str(exc)))
            await self._enqueue_local_retry(config, payload, attempt=0)
            return False

    async def _enqueue_local_retry(self, config: DBChangeTriggerConfig, payload: dict[str, Any], attempt: int) -> None:
        if self._local_retry_queue.full():
            dropped = await self._local_retry_queue.get()
            self._local_retry_queue.task_done()
            logger.warning("db_change retry queue full, dropping oldest event for %s", dropped[0].event_name)
        await self._local_retry_queue.put((config, payload, attempt))

    async def _move_to_dlq(
        self,
        config: DBChangeTriggerConfig,
        payload: dict[str, Any],
        attempt: int,
        exc: Exception,
    ) -> None:
        self._dlq_events.append(
            {
                "event_name": config.event_name,
                "tenant_id": config.tenant_id,
                "payload": _redact_sensitive_data(payload),
                "attempt": attempt,
                "error": _redact_sensitive_text(str(exc)),
            }
        )
        safe_error = _redact_sensitive_text(str(exc))
        logger.warning(
            "db_change retries exhausted for %s after %d attempts, moved to DLQ: %s",
            config.event_name,
            attempt,
            safe_error,
        )

    async def _retry_loop(self) -> None:
        while True:
            config, payload, attempt = await self._local_retry_queue.get()
            try:
                await self._agent_runtime.trigger_event(
                    event_name=config.event_name,
                    payload=payload,
                    focus=config.focus,
                    tenant_id=config.tenant_id,
                )
            except Exception as exc:
                next_attempt = attempt + 1
                if next_attempt >= self._max_retry_attempts:
                    await self._move_to_dlq(config, payload, next_attempt, exc)
                else:
                    logger.warning(
                        "db_change retry failed for %s, requeueing: %s",
                        config.event_name,
                        _redact_sensitive_text(str(exc)),
                    )
                    await asyncio.sleep(self._retry_interval_seconds)
                    await self._enqueue_local_retry(config, payload, next_attempt)
            finally:
                self._local_retry_queue.task_done()

    @staticmethod
    def _is_payload_oversized(event: DBChangeEvent, max_payload_bytes: int) -> bool:
        started = monotonic()
        serialized = json.dumps(event.payload, ensure_ascii=False)
        if monotonic() - started > 0.05:
            logger.debug("db_change payload serialization took >50ms on channel %s", event.channel)
        return len(serialized.encode("utf-8")) > max_payload_bytes
