"""Execution trigger service for webhook pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, cast
from uuid import uuid4

from owlclaw.triggers.webhook.types import AgentInput, ExecutionOptions, ExecutionResult, ExecutionStatus, RetryPolicy


class RuntimeInvokerProtocol(Protocol):
    """Protocol for agent runtime invocation adapter."""

    async def trigger(self, input_data: AgentInput) -> dict[str, Any]: ...


@dataclass(slots=True)
class _IdempotencyEntry:
    result: ExecutionResult
    expires_at: datetime


class ExecutionTrigger:
    """Trigger Agent Runtime with idempotency and retry guarantees."""

    def __init__(
        self,
        runtime: RuntimeInvokerProtocol,
        *,
        sleeper: Any = asyncio.sleep,
        max_idempotency_entries: int = 1024,
        max_execution_entries: int = 2048,
    ) -> None:
        self._runtime = runtime
        self._sleeper = sleeper
        self._max_idempotency_entries = max(1, int(max_idempotency_entries))
        self._max_execution_entries = max(1, int(max_execution_entries))
        self._idempotency: dict[str, _IdempotencyEntry] = {}
        self._executions: dict[str, ExecutionResult] = {}
        self._idempotency_locks: dict[str, asyncio.Lock] = {}

    async def check_idempotency(self, key: str) -> ExecutionResult | None:
        entry = self._idempotency.get(key)
        if entry is None:
            return None
        if entry.expires_at <= datetime.now(timezone.utc):
            self._idempotency.pop(key, None)
            return None
        return entry.result

    async def record_idempotency(self, key: str, result: ExecutionResult, ttl_seconds: float) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        self._idempotency[key] = _IdempotencyEntry(result=result, expires_at=expires_at)
        self._trim_idempotency_cache()

    async def trigger(self, input_data: AgentInput, options: ExecutionOptions) -> ExecutionResult:
        if options.idempotency_key:
            return await self._trigger_with_idempotency_lock(input_data, options)
        return await self._trigger_internal(input_data, options)

    async def _trigger_with_idempotency_lock(self, input_data: AgentInput, options: ExecutionOptions) -> ExecutionResult:
        assert options.idempotency_key is not None
        key = options.idempotency_key
        lock = self._idempotency_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._idempotency_locks[key] = lock
        async with lock:
            return await self._trigger_internal(input_data, options)

    async def _trigger_internal(self, input_data: AgentInput, options: ExecutionOptions) -> ExecutionResult:
        if options.idempotency_key:
            existing = await self.check_idempotency(options.idempotency_key)
            if existing is not None:
                return existing
        retry_policy = options.retry_policy or RetryPolicy(max_attempts=1, initial_delay_ms=0, max_delay_ms=0, backoff_multiplier=1.0)
        attempt = 0
        last_error: dict[str, Any] | None = None
        while attempt < retry_policy.max_attempts:
            attempt += 1
            try:
                runtime_result = await self._invoke_runtime(input_data, timeout_seconds=options.timeout_seconds)
                result = self._to_execution_result(runtime_result, options.mode)
                self._executions[result.execution_id] = result
                self._trim_execution_cache()
                if options.idempotency_key:
                    await self.record_idempotency(options.idempotency_key, result, ttl_seconds=3600)
                return result
            except Exception as exc:
                last_error = {"type": type(exc).__name__, "message": str(exc)}
                if attempt >= retry_policy.max_attempts or not _is_retriable_error(exc):
                    break
                delay = _retry_delay_seconds(retry_policy, attempt)
                await self._sleeper(delay)
        status_code = 503 if last_error is not None and last_error.get("type") in {"TimeoutError", "ConnectionError"} else 500
        failed = ExecutionResult(
            execution_id=str(uuid4()),
            status="failed",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            output=None,
            error=None if last_error is None else {**last_error, "status_code": status_code},
        )
        self._executions[failed.execution_id] = failed
        self._trim_execution_cache()
        if options.idempotency_key:
            await self.record_idempotency(options.idempotency_key, failed, ttl_seconds=3600)
        return failed

    async def get_execution_status(self, execution_id: str) -> ExecutionResult | None:
        return self._executions.get(execution_id)

    def _trim_idempotency_cache(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [key for key, entry in self._idempotency.items() if entry.expires_at <= now]
        for key in expired:
            self._idempotency.pop(key, None)
            lock = self._idempotency_locks.get(key)
            if lock is not None and not lock.locked():
                self._idempotency_locks.pop(key, None)
        while len(self._idempotency) > self._max_idempotency_entries:
            oldest_key = next(iter(self._idempotency))
            self._idempotency.pop(oldest_key, None)
            lock = self._idempotency_locks.get(oldest_key)
            if lock is not None and not lock.locked():
                self._idempotency_locks.pop(oldest_key, None)
        if len(self._idempotency_locks) > self._max_idempotency_entries * 2:
            removable = [key for key, lock in self._idempotency_locks.items() if key not in self._idempotency and not lock.locked()]
            for key in removable:
                self._idempotency_locks.pop(key, None)

    def _trim_execution_cache(self) -> None:
        while len(self._executions) > self._max_execution_entries:
            oldest_key = next(iter(self._executions))
            self._executions.pop(oldest_key, None)

    async def _invoke_runtime(self, input_data: AgentInput, *, timeout_seconds: float | None) -> dict[str, Any]:
        coro = self._runtime.trigger(input_data)
        if timeout_seconds is None:
            return await coro
        return await asyncio.wait_for(coro, timeout=timeout_seconds)

    @staticmethod
    def _to_execution_result(runtime_result: dict[str, Any], mode: str) -> ExecutionResult:
        now = datetime.now(timezone.utc)
        execution_id = str(runtime_result.get("execution_id", uuid4()))
        output = runtime_result.get("output")
        if mode == "async":
            return ExecutionResult(
                execution_id=execution_id,
                status="accepted",
                started_at=now,
                completed_at=None,
                output=output,
                error=None,
            )
        return ExecutionResult(
            execution_id=execution_id,
            status=_normalize_execution_status(runtime_result.get("status", "completed")),
            started_at=now,
            completed_at=now,
            output=output,
            error=runtime_result.get("error"),
        )


def _is_retriable_error(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError | ConnectionError)


def _retry_delay_seconds(policy: RetryPolicy, attempt: int) -> float:
    raw = policy.initial_delay_ms * (policy.backoff_multiplier ** (attempt - 1))
    bounded = min(raw, policy.max_delay_ms)
    return max(0.0, float(bounded) / 1000.0)


def _normalize_execution_status(value: object) -> ExecutionStatus:
    normalized = str(value)
    if normalized not in {"accepted", "running", "completed", "failed"}:
        return "failed"
    return cast(ExecutionStatus, normalized)
