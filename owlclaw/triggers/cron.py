"""Cron trigger registry — persistent Agent-driven cron scheduling.

Provides CronTriggerRegistry and associated data models for registering
@app.cron decorated functions, validating cron expressions via croniter,
and lifecycle management of scheduled tasks.

Hatchet workflow creation is deferred to a separate start() phase so that
registration is always safe to call at import time even without a live
Hatchet connection.
"""

from __future__ import annotations

import asyncio
import heapq
import inspect
import logging
import random
import traceback as _traceback
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from itertools import count
from typing import TYPE_CHECKING, Any, cast

from croniter import croniter  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from owlclaw.agent.runtime import AgentRuntime
    from owlclaw.governance.ledger import Ledger
    from owlclaw.integrations.hatchet import HatchetClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models — Task 1
# ---------------------------------------------------------------------------


class ExecutionStatus(str, Enum):
    """Execution status for a single cron run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    FALLBACK = "fallback"


@dataclass
class CronTriggerConfig:
    """Configuration for a single cron trigger."""

    event_name: str
    expression: str
    description: str | None = None

    # Agent guidance
    focus: str | None = None

    # Fallback / migration
    fallback_handler: Callable | None = None
    fallback_strategy: str = "on_failure"
    migration_weight: float = 1.0

    # Governance
    max_cost_per_run: float | None = None
    max_daily_cost: float | None = None
    max_duration: int | None = None
    cooldown_seconds: int = 0
    max_daily_runs: int | None = None

    # Reliability
    retry_on_failure: bool = True
    max_retries: int = 3
    retry_delay_seconds: int = 60

    # Metadata
    priority: int = 0
    tags: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class CronExecution:
    """Record of a single cron trigger execution."""

    execution_id: str
    event_name: str
    triggered_at: datetime
    status: ExecutionStatus
    context: dict[str, Any]
    decision_mode: str = "agent"

    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None

    agent_run_id: str | None = None
    llm_calls: int = 0
    cost_usd: float = 0.0

    governance_checks: dict[str, Any] = field(default_factory=dict)
    skip_reason: str | None = None

    error_message: str | None = None
    error_traceback: str | None = None
    retry_count: int = 0


class FocusManager:
    """Load and format skills with optional focus filtering."""

    def __init__(self, skills_manager: Any) -> None:
        self.skills_manager = skills_manager

    async def load_skills_for_focus(self, focus: str | None) -> list[Any]:
        """Load skills and filter by focus when provided."""
        list_skills = getattr(self.skills_manager, "list_skills", None)
        if not callable(list_skills):
            return []
        skills = list_skills()
        if inspect.isawaitable(skills):
            skills = await skills
        if not isinstance(skills, list):
            return []
        if not focus:
            return [skill for skill in skills if hasattr(skill, "name") and hasattr(skill, "description")]
        normalized_focus = focus.strip().lower()
        if not normalized_focus:
            return [skill for skill in skills if hasattr(skill, "name") and hasattr(skill, "description")]
        return [skill for skill in skills if self._skill_matches_focus(skill, normalized_focus)]

    def _skill_matches_focus(self, skill: Any, focus: str) -> bool:
        """Return whether a single skill matches the given focus."""
        target = focus.strip().lower()
        if not target:
            return True
        try:
            direct_focus = getattr(skill, "focus", [])
            direct_values: list[str]
            if isinstance(direct_focus, str):
                direct_values = [direct_focus]
            elif isinstance(direct_focus, list):
                direct_values = [str(value) for value in direct_focus]
            else:
                direct_values = []
            if any(value.strip().lower() == target for value in direct_values if value.strip()):
                return True

            metadata = getattr(skill, "metadata", {})
            if isinstance(metadata, dict):
                meta_focus = metadata.get("focus", [])
                if isinstance(meta_focus, str):
                    meta_values = [meta_focus]
                elif isinstance(meta_focus, list):
                    meta_values = [str(value) for value in meta_focus]
                else:
                    meta_values = []
                if any(value.strip().lower() == target for value in meta_values if value.strip()):
                    return True
        except Exception:
            return False
        return False

    def build_agent_prompt(self, focus: str | None, skills: list[Any]) -> str:
        """Build a prompt snippet describing current focus and available skills."""
        prompt_parts: list[str] = []
        if focus and focus.strip():
            normalized_focus = focus.strip()
            prompt_parts.append(f"Current focus: {normalized_focus}")
            prompt_parts.append(f"You should prioritize actions related to {normalized_focus}.")
        else:
            prompt_parts.append("Current focus: none")
        prompt_parts.append("")
        prompt_parts.append("Available skills:")
        if not skills:
            prompt_parts.append("- (none)")
            return "\n".join(prompt_parts)
        for skill in skills:
            name = str(getattr(skill, "name", "")).strip()
            description = str(getattr(skill, "description", "")).strip()
            if not name:
                continue
            prompt_parts.append(f"- {name}: {description}")
        return "\n".join(prompt_parts)


class RetryStrategy:
    """Retry decision and delay helpers for cron execution."""

    @staticmethod
    def should_retry(
        *,
        error: Exception,
        retry_count: int,
        max_retries: int,
        retry_on_failure: bool,
    ) -> bool:
        """Return whether a failed execution should be retried."""
        if not retry_on_failure:
            return False
        if retry_count >= max_retries:
            return False
        return not isinstance(error, ValueError | TypeError)

    @staticmethod
    def calculate_delay(
        retry_count: int,
        *,
        base_delay_seconds: int,
        max_delay_seconds: int = 3600,
    ) -> int:
        """Calculate bounded exponential backoff delay in seconds."""
        delay = max(0, int(base_delay_seconds)) * (2 ** max(0, int(retry_count)))
        return int(min(delay, max(0, int(max_delay_seconds))))


class CircuitBreaker:
    """In-memory circuit breaker state for trigger-level failure protection."""

    def __init__(
        self,
        failure_threshold: float = 0.5,
        window_size: int = 10,
        *,
        state_store: Any | None = None,
        store_prefix: str = "cron:circuit_breaker",
    ) -> None:
        self.failure_threshold = max(0.0, min(float(failure_threshold), 1.0))
        self.window_size = max(1, int(window_size))
        self._open_events: set[str] = set()
        self._state_store = state_store
        self._store_prefix = store_prefix

    def _store_key(self, event_name: str) -> str:
        return f"{self._store_prefix}:{event_name}"

    def _read_store(self, event_name: str) -> bool | None:
        if self._state_store is None:
            return None
        getter = getattr(self._state_store, "get", None)
        if not callable(getter):
            return None
        value = getter(self._store_key(event_name))
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "open", "yes"}

    def _write_store(self, event_name: str, opened: bool) -> None:
        if self._state_store is None:
            return
        key = self._store_key(event_name)
        if opened:
            setter = getattr(self._state_store, "set", None)
            if callable(setter):
                setter(key, "1")
                return
        deleter = getattr(self._state_store, "delete", None)
        if callable(deleter):
            deleter(key)
            return
        setter = getattr(self._state_store, "set", None)
        if callable(setter):
            setter(key, "0")

    def check(self, event_name: str) -> tuple[bool, str]:
        """Return whether execution can proceed for the given event."""
        stored = self._read_store(event_name)
        if stored:
            self._open_events.add(event_name)
        if event_name in self._open_events:
            return False, "Circuit breaker is open"
        return True, ""

    def evaluate(self, event_name: str, records: list[Any]) -> bool:
        """Update breaker state from recent records and return open/closed."""
        if not records:
            self.close(event_name)
            return False
        statuses = [str(getattr(record, "status", "")).strip().lower() for record in records[: self.window_size]]
        if not statuses:
            self.close(event_name)
            return False
        failed = sum(1 for status in statuses if status not in {"success", "fallback"})
        failure_rate = failed / len(statuses)
        if len(statuses) >= self.window_size and failure_rate > self.failure_threshold:
            self.open(event_name)
            return True
        self.close(event_name)
        return False

    def open(self, event_name: str) -> None:
        """Open the breaker for an event."""
        self._open_events.add(event_name)
        self._write_store(event_name, True)

    def close(self, event_name: str) -> None:
        """Close the breaker for an event."""
        self._open_events.discard(event_name)
        self._write_store(event_name, False)

    def is_open(self, event_name: str) -> bool:
        """Return whether breaker is open for an event."""
        return event_name in self._open_events

    def open_count(self) -> int:
        """Return number of currently open event breakers."""
        return len(self._open_events)


class ErrorNotifier:
    """Failure notification helper with low-noise sampling policy."""

    def __init__(self, channels: dict[str, Callable[[str], Any]] | None = None) -> None:
        self.channels = channels or {}

    @staticmethod
    def _should_notify(failure_count: int) -> bool:
        return int(failure_count) in {1, 3, 5}

    @staticmethod
    def _build_message(event_name: str, failure_count: int, error: str) -> str:
        return (
            f"Cron trigger '{event_name}' failed {failure_count} times. "
            f"Latest error: {error}"
        )

    def notify_failure(self, event_name: str, failure_count: int, error: str) -> None:
        """Emit a notification log for selected failure counts."""
        if not self._should_notify(failure_count):
            return
        message = self._build_message(event_name, failure_count, error)
        if not self.channels:
            logger.warning(message)
            return
        for channel_name, channel in self.channels.items():
            try:
                result = channel(message)
                if inspect.isawaitable(result):
                    coroutine_result = cast(Coroutine[Any, Any, Any], result)
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        asyncio.run(coroutine_result)
                    else:
                        loop.create_task(coroutine_result)
            except Exception as exc:
                logger.exception(
                    "Error notifier channel '%s' failed for event '%s': %s",
                    channel_name,
                    event_name,
                    exc,
                )


@dataclass(order=True)
class PrioritizedTask:
    """Internal scheduler record ordered by priority then insertion order."""

    sort_index: tuple[int, int]
    task_id: str = field(compare=False)
    task_factory: Callable[[], Awaitable[Any]] = field(compare=False)
    priority: int = field(compare=False)

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        priority: int,
        sequence: int,
        task_factory: Callable[[], Awaitable[Any]],
    ) -> PrioritizedTask:
        # Use negative priority so larger number runs first in min-heap.
        return cls(
            sort_index=(-int(priority), int(sequence)),
            task_id=task_id,
            task_factory=task_factory,
            priority=int(priority),
        )


class ConcurrencyController:
    """Limit concurrent task execution with active task tracking."""

    def __init__(self, max_concurrency: int = 10) -> None:
        self.max_concurrency = max(1, int(max_concurrency))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    async def execute_with_limit(
        self,
        task_id: str,
        task_factory: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Execute a task under the concurrency limit and return its result."""
        normalized_task_id = str(task_id).strip() or str(uuid.uuid4())

        async def _runner() -> Any:
            async with self._semaphore:
                return await task_factory()

        task = asyncio.create_task(_runner(), name=f"cron-concurrency:{normalized_task_id}")
        async with self._lock:
            self._active_tasks[normalized_task_id] = task
        try:
            return await task
        finally:
            async with self._lock:
                current = self._active_tasks.get(normalized_task_id)
                if current is task:
                    self._active_tasks.pop(normalized_task_id, None)

    def get_active_count(self) -> int:
        """Return number of currently active tasks."""
        return len(self._active_tasks)

    async def wait_all(self) -> None:
        """Wait for all active tasks to finish (best effort snapshot)."""
        async with self._lock:
            tasks = list(self._active_tasks.values())
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


class PriorityScheduler:
    """Priority queue scheduler backed by heapq and async lock."""

    def __init__(self) -> None:
        self._queue: list[PrioritizedTask] = []
        self._lock = asyncio.Lock()
        self._sequence = count()

    async def schedule(
        self,
        *,
        task_id: str,
        task_factory: Callable[[], Awaitable[Any]],
        priority: int = 0,
    ) -> None:
        """Push a task into the priority queue."""
        normalized_task_id = str(task_id).strip() or str(uuid.uuid4())
        item = PrioritizedTask.create(
            task_id=normalized_task_id,
            priority=priority,
            sequence=next(self._sequence),
            task_factory=task_factory,
        )
        async with self._lock:
            heapq.heappush(self._queue, item)

    async def execute_next(self) -> Any | None:
        """Pop and execute the next task by priority; return None when empty."""
        async with self._lock:
            if not self._queue:
                return None
            item = heapq.heappop(self._queue)
        return await item.task_factory()

    async def size(self) -> int:
        """Return pending queue size."""
        async with self._lock:
            return len(self._queue)


class CronCache:
    """In-memory cache for execution samples and derived stats."""

    def __init__(self, stats_ttl_seconds: int = 60, execution_cache_size: int = 100) -> None:
        self._stats_ttl_seconds = max(1, int(stats_ttl_seconds))
        self._execution_cache_size = max(1, int(execution_cache_size))
        self._execution_records: dict[str, deque[dict[str, Any]]] = {}
        self._stats_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}

    def record_execution(self, event_name: str, record: dict[str, Any]) -> None:
        """Append one execution record into per-event bounded cache."""
        key = str(event_name).strip()
        if not key:
            return
        bucket = self._execution_records.setdefault(key, deque(maxlen=self._execution_cache_size))
        bucket.append(dict(record))

    def get_execution_records(self, event_name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get cached execution records in reverse-chronological order."""
        key = str(event_name).strip()
        if not key:
            return []
        safe_limit = max(1, int(limit))
        records = list(self._execution_records.get(key, ()))
        return list(reversed(records[-safe_limit:]))

    def set_stats(self, event_name: str, stats: dict[str, Any], ttl_seconds: int | None = None) -> None:
        """Store computed stats for event_name with TTL."""
        key = str(event_name).strip()
        if not key:
            return
        ttl = self._stats_ttl_seconds if ttl_seconds is None else max(1, int(ttl_seconds))
        expires_at = datetime.now(timezone.utc).replace(microsecond=0)
        expires_at = expires_at + timedelta(seconds=ttl)
        self._stats_cache[key] = (expires_at, dict(stats))

    def get_stats(self, event_name: str) -> dict[str, Any] | None:
        """Return stats cache entry when present and not expired."""
        key = str(event_name).strip()
        if not key:
            return None
        cached = self._stats_cache.get(key)
        if cached is None:
            return None
        expires_at, stats = cached
        if datetime.now(timezone.utc) > expires_at:
            self._stats_cache.pop(key, None)
            return None
        return dict(stats)

    def invalidate(self, event_name: str | None = None) -> None:
        """Invalidate one event cache or clear all caches."""
        if event_name is None:
            self._execution_records.clear()
            self._stats_cache.clear()
            self._next_trigger_time_cached.cache_clear()
            return
        key = str(event_name).strip()
        if not key:
            return
        self._execution_records.pop(key, None)
        self._stats_cache.pop(key, None)
        self._next_trigger_time_cached.cache_clear()

    @staticmethod
    @lru_cache(maxsize=2048)
    def _next_trigger_time_cached(expression: str, base_timestamp: int) -> str | None:
        base = datetime.fromtimestamp(base_timestamp, tz=timezone.utc)
        try:
            next_dt = croniter(expression, base).get_next(datetime)
        except Exception:
            return None
        return str(next_dt.isoformat())

    def next_trigger_time(self, expression: str, base_time: datetime | None = None) -> str | None:
        """Compute next trigger timestamp using cached croniter results."""
        base = base_time or datetime.now(timezone.utc)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        base_ts = int(base.timestamp())
        return self._next_trigger_time_cached(expression, base_ts)


class BatchOperations:
    """Batch helpers for ledger recording and query fan-out."""

    def __init__(self, batch_size: int = 100) -> None:
        self.batch_size = max(1, int(batch_size))

    async def batch_record_executions(
        self,
        *,
        ledger: Any,
        records: list[dict[str, Any]],
    ) -> int:
        """Record executions in chunks; returns number of accepted records."""
        if not records:
            return 0
        written = 0
        batch_record = getattr(ledger, "batch_record_executions", None)
        has_explicit_batch_api = "batch_record_executions" in vars(ledger)
        for start in range(0, len(records), self.batch_size):
            chunk = records[start : start + self.batch_size]
            if callable(batch_record) and has_explicit_batch_api:
                result = batch_record(chunk)
                if inspect.isawaitable(result):
                    await result
                written += len(chunk)
                continue
            record_execution = getattr(ledger, "record_execution", None)
            if not callable(record_execution):
                raise AttributeError("ledger must provide record_execution or batch_record_executions")
            for item in chunk:
                await record_execution(**item)
                written += 1
        return written

    async def batch_query_executions(
        self,
        *,
        ledger: Any,
        tenant_id: str,
        filters_list: list[Any],
    ) -> list[list[Any]]:
        """Query execution records in batches and preserve input ordering."""
        if not filters_list:
            return []
        query_records = getattr(ledger, "query_records", None)
        if not callable(query_records):
            raise AttributeError("ledger must provide query_records")
        out: list[list[Any]] = []
        for start in range(0, len(filters_list), self.batch_size):
            chunk = filters_list[start : start + self.batch_size]
            for filters in chunk:
                records = await query_records(tenant_id=tenant_id, filters=filters)
                out.append(list(records))
        return out


CRON_METRICS_SAMPLES_MAXLEN = 10_000


class CronMetrics:
    """Metrics collector with optional Prometheus backend and in-memory fallback."""

    def __init__(self) -> None:
        self.execution_counts: dict[tuple[str, str, str], int] = {}
        self.duration_samples: deque[float] = deque(maxlen=CRON_METRICS_SAMPLES_MAXLEN)
        self.delay_samples: deque[float] = deque(maxlen=CRON_METRICS_SAMPLES_MAXLEN)
        self.cost_samples: deque[float] = deque(maxlen=CRON_METRICS_SAMPLES_MAXLEN)
        self.llm_calls_total = 0
        self.active_tasks = 0
        self.circuit_breaker_open = 0
        self._prometheus_available = False
        self.executions_total: Any | None = None
        self.execution_duration_seconds: Any | None = None
        self.trigger_delay_seconds: Any | None = None
        self.execution_cost_usd: Any | None = None
        self.llm_calls_total_counter: Any | None = None
        self.active_tasks_gauge: Any | None = None
        self.circuit_breaker_open_gauge: Any | None = None

        try:
            from prometheus_client import Counter, Gauge, Histogram  # type: ignore[import-not-found]

            self.executions_total = Counter(
                "owlclaw_cron_executions_total",
                "Total cron executions",
                ["event_name", "status", "decision_mode"],
            )
            self.execution_duration_seconds = Histogram(
                "owlclaw_cron_execution_duration_seconds",
                "Cron execution duration in seconds",
                ["event_name"],
            )
            self.trigger_delay_seconds = Histogram(
                "owlclaw_cron_trigger_delay_seconds",
                "Delay between scheduled and actual trigger",
                ["event_name"],
            )
            self.execution_cost_usd = Histogram(
                "owlclaw_cron_execution_cost_usd",
                "Estimated cron execution cost in USD",
                ["event_name"],
            )
            self.llm_calls_total_counter = Counter(
                "owlclaw_cron_llm_calls_total",
                "Total LLM calls made by cron executions",
                ["event_name"],
            )
            self.active_tasks_gauge = Gauge(
                "owlclaw_cron_active_tasks",
                "Number of currently active cron tasks",
            )
            self.circuit_breaker_open_gauge = Gauge(
                "owlclaw_cron_circuit_breaker_open",
                "Number of open cron circuit breakers",
            )
            self._prometheus_available = True
        except Exception:
            self._prometheus_available = False

    def record_execution(self, event_name: str, execution: CronExecution) -> None:
        """Record execution counters, durations, costs, and LLM calls."""
        key = (event_name, execution.status.value, execution.decision_mode)
        self.execution_counts[key] = self.execution_counts.get(key, 0) + 1
        if execution.duration_seconds is not None:
            self.duration_samples.append(execution.duration_seconds)
        if execution.cost_usd > 0:
            self.cost_samples.append(execution.cost_usd)
        self.llm_calls_total += max(0, int(execution.llm_calls))

        if not self._prometheus_available:
            return
        if self.executions_total is not None:
            self.executions_total.labels(event_name, execution.status.value, execution.decision_mode).inc()
        if execution.duration_seconds is not None and self.execution_duration_seconds is not None:
            self.execution_duration_seconds.labels(event_name).observe(execution.duration_seconds)
        if self.execution_cost_usd is not None:
            self.execution_cost_usd.labels(event_name).observe(execution.cost_usd)
        if self.llm_calls_total_counter is not None:
            self.llm_calls_total_counter.labels(event_name).inc(max(0, int(execution.llm_calls)))

    def record_trigger_delay(self, event_name: str, delay_seconds: float) -> None:
        """Record trigger delay metric sample."""
        safe_delay = max(0.0, float(delay_seconds))
        self.delay_samples.append(safe_delay)
        if self._prometheus_available and self.trigger_delay_seconds is not None:
            self.trigger_delay_seconds.labels(event_name).observe(safe_delay)

    def set_active_tasks(self, count: int) -> None:
        """Set active cron task count."""
        self.active_tasks = max(0, int(count))
        if self._prometheus_available and self.active_tasks_gauge is not None:
            self.active_tasks_gauge.set(self.active_tasks)

    def set_circuit_breaker_open(self, count: int) -> None:
        """Set open circuit breaker count."""
        self.circuit_breaker_open = max(0, int(count))
        if self._prometheus_available and self.circuit_breaker_open_gauge is not None:
            self.circuit_breaker_open_gauge.set(self.circuit_breaker_open)


class CronLogger:
    """Structured-style logging helper based on stdlib logging."""

    def log_registration(self, config: CronTriggerConfig) -> None:
        logger.info(
            "cron_registration event_name=%s expression=%s focus=%s",
            config.event_name,
            config.expression,
            config.focus,
        )

    def log_trigger(self, event_name: str, context: dict[str, Any]) -> None:
        logger.info("cron_trigger event_name=%s context=%s", event_name, context)

    def log_execution_start(self, event_name: str, execution_id: str, mode: str) -> None:
        logger.info(
            "cron_execution_start event_name=%s execution_id=%s mode=%s",
            event_name,
            execution_id,
            mode,
        )

    def log_execution_complete(self, event_name: str, execution_id: str, status: str, duration: float | None) -> None:
        logger.info(
            "cron_execution_complete event_name=%s execution_id=%s status=%s duration_seconds=%s",
            event_name,
            execution_id,
            status,
            duration,
        )

    def log_execution_failed(self, event_name: str, execution_id: str, error: str) -> None:
        logger.error(
            "cron_execution_failed event_name=%s execution_id=%s error=%s",
            event_name,
            execution_id,
            error,
        )

    def log_governance_skip(self, event_name: str, reason: str) -> None:
        logger.warning("cron_governance_skip event_name=%s reason=%s", event_name, reason)

    def log_circuit_breaker_open(self, event_name: str, failure_rate: float) -> None:
        logger.warning(
            "cron_circuit_breaker_open event_name=%s failure_rate=%.4f",
            event_name,
            failure_rate,
        )


class CronGovernance:
    """Governance adapter for CronTriggerRegistry."""

    def __init__(self, registry: CronTriggerRegistry) -> None:
        self.registry = registry

    async def check_constraints(
        self,
        config: CronTriggerConfig,
        execution: CronExecution,
        ledger: Ledger | None,
        tenant_id: str,
    ) -> tuple[bool, str]:
        return await self.registry._check_constraints_core(config, execution, ledger, tenant_id)

    async def record_execution(
        self,
        ledger: Ledger,
        config: CronTriggerConfig,
        execution: CronExecution,
        tenant_id: str,
        *,
        agent_id: str,
    ) -> None:
        await self.registry._record_to_ledger(ledger, config, execution, tenant_id, agent_id=agent_id)

    async def update_circuit_breaker(
        self,
        config: CronTriggerConfig,
        ledger: Ledger | None,
        tenant_id: str,
    ) -> None:
        if ledger is None:
            return
        try:
            records = await self.registry._get_recent_executions(
                ledger=ledger,
                tenant_id=tenant_id,
                event_name=config.event_name,
                limit=self.registry._circuit_breaker.window_size,
            )
            opened = self.registry._circuit_breaker.evaluate(config.event_name, records)
            if opened:
                statuses = [str(getattr(record, "status", "")).strip().lower() for record in records]
                failed = sum(1 for status in statuses if status not in {"success", "fallback"})
                failure_rate = failed / len(statuses) if statuses else 0.0
                self.registry._cron_logger.log_circuit_breaker_open(config.event_name, failure_rate)
                self.registry._error_notifier.notify_failure(
                    config.event_name,
                    failed,
                    "circuit breaker opened due to high failure rate",
                )
            self.registry._metrics.set_circuit_breaker_open(self.registry._circuit_breaker.open_count())
        except Exception as exc:
            logger.exception("Failed to update circuit breaker for '%s': %s", config.event_name, exc)


class CronHealthCheck:
    """Health status provider for cron subsystem."""

    def __init__(self, registry: CronTriggerRegistry) -> None:
        self.registry = registry

    def check_health(self) -> dict[str, Any]:
        total = len(self.registry._triggers)
        enabled = sum(1 for config in self.registry._triggers.values() if config.enabled)
        disabled = total - enabled
        hatchet_connected = self.registry._hatchet_client is not None
        open_breakers = self.registry._circuit_breaker.open_count()
        if total == 0:
            status = "unhealthy"
        elif (not hatchet_connected) or (open_breakers > 0):
            status = "degraded"
        else:
            status = "healthy"
        return {
            "status": status,
            "hatchet_connected": hatchet_connected,
            "total_triggers": total,
            "enabled_triggers": enabled,
            "disabled_triggers": disabled,
            "open_circuit_breakers": open_breakers,
        }


# ---------------------------------------------------------------------------
# CronTriggerRegistry — Task 2
# ---------------------------------------------------------------------------


class CronTriggerRegistry:
    """Registry for cron triggers.

    Manages validation, storage, and lifecycle of all @app.cron decorated
    functions.  Hatchet workflow objects are stored separately and populated
    on start().
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self._triggers: dict[str, CronTriggerConfig] = {}
        self._hatchet_workflows: dict[str, Callable[..., Any]] = {}
        self._hatchet_client: HatchetClient | None = None
        self._ledger: Ledger | None = None
        self._tenant_id: str = "default"
        self._recent_executions: dict[str, deque[tuple[str, float | None]]] = {}
        self._metrics = CronMetrics()
        self._cron_logger = CronLogger()
        self._circuit_breaker = CircuitBreaker()
        self._error_notifier = ErrorNotifier()
        self._governance = CronGovernance(self)
        self._health_check = CronHealthCheck(self)
        self._active_tasks = 0
        self._active_runs: set[tuple[str, str]] = set()
        self._active_runs_lock = asyncio.Lock()
        self._concurrency_controller = ConcurrencyController()
        self._default_trigger_kwargs: dict[str, Any] = {}
        self._runtime_cron_enabled = True
        self._notification_channels: list[str] = []

    def apply_settings(self, settings: dict[str, Any] | None) -> None:
        """Apply runtime defaults from `config.triggers` settings."""
        if not isinstance(settings, dict):
            return
        cron_cfg = settings.get("cron", {}) if isinstance(settings.get("cron"), dict) else {}
        governance_cfg = settings.get("governance", {}) if isinstance(settings.get("governance"), dict) else {}
        retry_cfg = settings.get("retry", {}) if isinstance(settings.get("retry"), dict) else {}
        notifications_cfg = settings.get("notifications", {}) if isinstance(settings.get("notifications"), dict) else {}

        defaults: dict[str, Any] = {}
        if "max_daily_runs" in governance_cfg:
            defaults["max_daily_runs"] = governance_cfg["max_daily_runs"]
        if "max_daily_cost" in governance_cfg:
            defaults["max_daily_cost"] = governance_cfg["max_daily_cost"]
        if "cooldown_seconds" in governance_cfg:
            defaults["cooldown_seconds"] = governance_cfg["cooldown_seconds"]
        if "retry_on_failure" in retry_cfg:
            defaults["retry_on_failure"] = retry_cfg["retry_on_failure"]
        if "max_retries" in retry_cfg:
            defaults["max_retries"] = retry_cfg["max_retries"]
        if "retry_delay_seconds" in retry_cfg:
            defaults["retry_delay_seconds"] = retry_cfg["retry_delay_seconds"]

        self._default_trigger_kwargs = defaults
        self._runtime_cron_enabled = bool(cron_cfg.get("enabled", True))
        max_concurrent = cron_cfg.get("max_concurrent")
        if isinstance(max_concurrent, int) and max_concurrent > 0:
            self._concurrency_controller = ConcurrencyController(max_concurrency=max_concurrent)
        channels = notifications_cfg.get("channels", [])
        self._notification_channels = (
            [str(ch).strip() for ch in channels if str(ch).strip()]
            if isinstance(channels, list)
            else []
        )

        logger.info(
            "Applied cron runtime settings enabled=%s default_kwargs=%s channels=%s",
            self._runtime_cron_enabled,
            sorted(self._default_trigger_kwargs.keys()),
            self._notification_channels,
        )

    async def wait_for_all_tasks(self, timeout_seconds: float = 10.0) -> None:
        """Wait for in-flight cron tasks to finish up to timeout."""
        deadline = asyncio.get_running_loop().time() + max(0.1, float(timeout_seconds))
        while self._active_tasks > 0 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)

    async def _acquire_run_slot(self, tenant_id: str, event_name: str) -> bool:
        key = (tenant_id, event_name)
        async with self._active_runs_lock:
            if key in self._active_runs:
                return False
            self._active_runs.add(key)
            return True

    async def _release_run_slot(self, tenant_id: str, event_name: str) -> None:
        key = (tenant_id, event_name)
        async with self._active_runs_lock:
            self._active_runs.discard(key)

    # ------------------------------------------------------------------
    # Task 2.2 — cron expression validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_cron_expression(expression: str) -> bool:
        """Return True if *expression* is a valid 5-field cron expression."""
        try:
            return bool(croniter.is_valid(expression))
        except Exception:
            return False

    @staticmethod
    def _normalize_event_name(event_name: str) -> str:
        if not isinstance(event_name, str):
            raise ValueError("event_name must be a non-empty string")
        normalized = event_name.strip()
        if not normalized:
            raise ValueError("event_name must not be empty")
        return normalized

    @staticmethod
    def _normalize_tenant_id(tenant_id: str) -> str:
        if not isinstance(tenant_id, str):
            raise ValueError("tenant_id must be a non-empty string")
        normalized = tenant_id.strip()
        if not normalized:
            raise ValueError("tenant_id must be a non-empty string")
        return normalized

    # ------------------------------------------------------------------
    # Task 2.4 — trigger registration
    # ------------------------------------------------------------------

    def register(
        self,
        event_name: str,
        expression: str,
        focus: str | None = None,
        fallback_handler: Callable | None = None,
        **kwargs: Any,
    ) -> None:
        """Register a cron trigger.

        Args:
            event_name: Unique event identifier.
            expression: 5-field cron expression (e.g. ``"0 * * * *"``).
            focus: Optional focus tag to narrow Agent skill loading.
            fallback_handler: Callable invoked when fallback strategy fires.
            **kwargs: Additional CronTriggerConfig fields.

        Raises:
            ValueError: If *event_name* is already registered or *expression*
                is invalid.
        """
        event_name = self._normalize_event_name(event_name)
        if not isinstance(expression, str):
            raise ValueError("expression must be a non-empty string")
        expression = expression.strip()
        if not expression:
            raise ValueError("expression must not be empty")
        if isinstance(focus, str):
            focus = focus.strip() or None

        if event_name in self._triggers:
            raise ValueError(f"Cron trigger '{event_name}' is already registered")

        if not self._validate_cron_expression(expression):
            raise ValueError(f"Invalid cron expression: '{expression}'")

        merged_kwargs = {**self._default_trigger_kwargs, **kwargs}

        migration_weight = merged_kwargs.get("migration_weight", 1.0)
        if (
            isinstance(migration_weight, bool)
            or not isinstance(migration_weight, int | float | Decimal)
            or migration_weight < 0.0
            or migration_weight > 1.0
        ):
            raise ValueError("migration_weight must be a float between 0.0 and 1.0")
        merged_kwargs["migration_weight"] = float(migration_weight)

        if not self._runtime_cron_enabled:
            merged_kwargs["enabled"] = False

        config = CronTriggerConfig(
            event_name=event_name,
            expression=expression,
            focus=focus,
            fallback_handler=fallback_handler,
            **merged_kwargs,
        )
        self._triggers[event_name] = config
        self._cron_logger.log_registration(config)

        logger.info(
            "Registered cron trigger event_name=%s expression=%s focus=%s",
            event_name,
            expression,
            focus,
        )

    # ------------------------------------------------------------------
    # Task 2.5 — query methods
    # ------------------------------------------------------------------

    def get_trigger(self, event_name: str) -> CronTriggerConfig | None:
        """Return the CronTriggerConfig for *event_name*, or None if missing."""
        try:
            event_name = self._normalize_event_name(event_name)
        except ValueError:
            return None
        return self._triggers.get(event_name)

    def list_triggers(self) -> list[CronTriggerConfig]:
        """Return all registered trigger configurations."""
        return list(self._triggers.values())

    def _record_recent_execution(
        self,
        event_name: str,
        status: str,
        duration_seconds: float | None,
    ) -> None:
        """Store lightweight recent execution stats for status reporting."""
        key = event_name.strip()
        if not key:
            return
        bucket = self._recent_executions.setdefault(key, deque(maxlen=50))
        bucket.append((status, duration_seconds))

    # ------------------------------------------------------------------
    # Management helpers (Task 8 pause/resume)
    # ------------------------------------------------------------------

    def get_all(self) -> dict[str, CronTriggerConfig]:
        """Return a shallow copy of the internal triggers dict."""
        return dict(self._triggers)

    def pause_trigger(self, event_name: str) -> None:
        """Disable a trigger so future cron fires are skipped.

        Raises:
            KeyError: If *event_name* is not registered.
        """
        try:
            event_name = self._normalize_event_name(event_name)
        except ValueError:
            raise KeyError("Cron trigger '' not found") from None
        config = self._triggers.get(event_name)
        if config is None:
            raise KeyError(f"Cron trigger '{event_name}' not found")
        config.enabled = False
        if self._hatchet_client is not None:
            pause_fn = getattr(self._hatchet_client, "pause_task", None)
            if callable(pause_fn):
                result = pause_fn(f"cron_{event_name}")
                if inspect.isawaitable(result):
                    self._dispatch_async(result)
        if self._ledger is not None:
            self._dispatch_async(
                self._record_management_action(
                    event_name=event_name,
                    action="pause",
                    tenant_id=self._tenant_id,
                )
            )
        logger.info("Paused cron trigger: %s", event_name)

    def resume_trigger(self, event_name: str) -> None:
        """Re-enable a previously paused trigger.

        Raises:
            KeyError: If *event_name* is not registered.
        """
        try:
            event_name = self._normalize_event_name(event_name)
        except ValueError:
            raise KeyError("Cron trigger '' not found") from None
        config = self._triggers.get(event_name)
        if config is None:
            raise KeyError(f"Cron trigger '{event_name}' not found")
        config.enabled = True
        if self._hatchet_client is not None:
            resume_fn = getattr(self._hatchet_client, "resume_task", None)
            if callable(resume_fn):
                result = resume_fn(f"cron_{event_name}")
                if inspect.isawaitable(result):
                    self._dispatch_async(result)
        if self._ledger is not None:
            self._dispatch_async(
                self._record_management_action(
                    event_name=event_name,
                    action="resume",
                    tenant_id=self._tenant_id,
                )
            )
        logger.info("Resumed cron trigger: %s", event_name)

    @staticmethod
    def _dispatch_async(awaitable: Any) -> None:
        """Run awaitable in running loop or fallback to asyncio.run."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(awaitable)
            return
        loop.create_task(awaitable)

    async def _record_management_action(
        self,
        *,
        event_name: str,
        action: str,
        tenant_id: str,
    ) -> None:
        """Record pause/resume management action to Ledger."""
        if self._ledger is None:
            return
        run_id = str(uuid.uuid4())
        try:
            await self._ledger.record_execution(
                tenant_id=tenant_id,
                agent_id=(self.app.name if self.app else "") or event_name,
                run_id=run_id,
                capability_name=event_name,
                task_type="cron_management",
                input_params={"action": action, "trigger_type": "management"},
                output_result={"event_name": event_name, "action": action},
                decision_reasoning=f"trigger_{action}",
                execution_time_ms=0,
                llm_model="",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status="success",
                error_message=None,
            )
        except Exception as exc:
            logger.exception("Failed to record trigger action for '%s': %s", event_name, exc)

    async def trigger_now(
        self,
        event_name: str,
        **kwargs: Any,
    ) -> str:
        """Trigger an immediate run of the cron workflow (manual trigger).

        Calls Hatchet to run the task now without waiting for the cron schedule.
        Must be called after start().

        Args:
            event_name: The registered trigger event name.
            **kwargs: Optional context passed to the run (e.g. focus override).

        Returns:
            Workflow run id from Hatchet.

        Raises:
            KeyError: If *event_name* is not registered.
            RuntimeError: If start() has not been called (no Hatchet client).
        """
        try:
            event_name = self._normalize_event_name(event_name)
        except ValueError:
            raise KeyError("Cron trigger '' not found") from None
        if event_name not in self._triggers:
            raise KeyError(f"Cron trigger '{event_name}' not found")
        if self._hatchet_client is None:
            raise RuntimeError(
                "trigger_now requires start() to be called with a Hatchet client"
            )
        run_task_now = getattr(self._hatchet_client, "run_task_now", None)
        if not callable(run_task_now):
            raise RuntimeError(
                "trigger_now requires Hatchet client with run_task_now() support"
            )
        task_name = f"cron_{event_name}"
        if "tenant_id" not in kwargs:
            kwargs["tenant_id"] = self._tenant_id
        else:
            kwargs["tenant_id"] = self._normalize_tenant_id(kwargs["tenant_id"])
        run_id = await run_task_now(task_name, **kwargs)
        self._record_recent_execution(event_name, "success", 0.0)
        if self._ledger is not None:
            try:
                await self._ledger.record_execution(
                    tenant_id=kwargs["tenant_id"],
                    agent_id=(self.app.name if self.app else "") or event_name,
                    run_id=str(run_id),
                    capability_name=event_name,
                    task_type="cron_manual_trigger",
                    input_params={"trigger_type": "manual", "kwargs": dict(kwargs)},
                    output_result={"run_id": str(run_id)},
                    decision_reasoning="manual_trigger",
                    execution_time_ms=0,
                    llm_model="",
                    llm_tokens_input=0,
                    llm_tokens_output=0,
                    estimated_cost=Decimal("0"),
                    status="success",
                    error_message=None,
                )
            except Exception as exc:
                logger.exception("Failed to record manual trigger for '%s': %s", event_name, exc)
        return str(run_id)

    def get_health_status(self) -> dict[str, Any]:
        """Return overall cron subsystem health status."""
        return self._health_check.check_health()

    async def get_execution_history(
        self,
        event_name: str,
        limit: int = 10,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return execution history for a trigger from Ledger.

        Queries Ledger for cron_execution records with capability_name=event_name.
        Must be called after start() with a Ledger.

        Trust boundary (same as P1-2 Console multi-tenant): *tenant_id* is taken
        from the parameter or from start() when None. In multi-tenant deployments,
        tenant should be derived from the authenticated caller (e.g. request context)
        rather than trusting a client-supplied value. See docs on Console tenant_id.

        Args:
            event_name: The registered trigger event name.
            limit: Max number of records to return (default 10).
            tenant_id: Override tenant; defaults to tenant from start().

        Returns:
            List of execution records (run_id, status, created_at, etc.).

        Raises:
            KeyError: If *event_name* is not registered.
            RuntimeError: If Ledger was not provided to start().
        """
        try:
            event_name = self._normalize_event_name(event_name)
        except ValueError:
            raise KeyError("Cron trigger '' not found") from None
        if event_name not in self._triggers:
            raise KeyError(f"Cron trigger '{event_name}' not found")
        if self._ledger is None:
            raise RuntimeError(
                "get_execution_history requires start() to be called with a Ledger"
            )
        from owlclaw.governance.ledger import LedgerQueryFilters

        if isinstance(limit, bool):
            safe_limit = 10
        else:
            try:
                safe_limit = int(limit)
            except (TypeError, ValueError):
                safe_limit = 10
        safe_limit = max(1, min(safe_limit, 100))

        tid = self._tenant_id if tenant_id is None else self._normalize_tenant_id(tenant_id)
        filters = LedgerQueryFilters(
            capability_name=event_name,
            limit=safe_limit,
            order_by="created_at DESC",
        )
        records = await self._ledger.query_records(tenant_id=tid, filters=filters)
        out: list[dict[str, Any]] = []
        for r in records:
            # Redact ledger error_message so callers do not receive raw exception text.
            error_display = "Execution failed." if (r.error_message and r.error_message.strip()) else None
            out.append({
                "run_id": r.run_id,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "execution_time_ms": r.execution_time_ms,
                "agent_run_id": (r.output_result or {}).get("agent_run_id"),
                "error_message": error_display,
            })
        return out

    def get_trigger_status(self, event_name: str) -> dict[str, Any]:
        """Return status for a trigger: config, enabled, next run time.

        Args:
            event_name: The registered trigger event name.

        Returns:
            Dict with event_name, enabled, expression, focus, next_run (ISO datetime).

        Raises:
            KeyError: If *event_name* is not registered.
        """
        try:
            event_name = self._normalize_event_name(event_name)
        except ValueError:
            raise KeyError("Cron trigger '' not found") from None
        config = self._triggers.get(event_name)
        if config is None:
            raise KeyError(f"Cron trigger '{event_name}' not found")
        next_run: str | None = None
        try:
            it = croniter(config.expression, datetime.now(timezone.utc))
            next_dt = it.get_next(datetime)
            next_run = next_dt.isoformat() if next_dt else None
        except Exception as exc:
            logger.warning(
                "Failed to compute next cron run event_name=%s expression=%s: %s",
                event_name,
                config.expression,
                exc,
            )
        samples = list(self._recent_executions.get(event_name, ()))
        sample_size = len(samples)
        success_statuses = {"success", "fallback"}
        success_count = sum(1 for status, _ in samples if status in success_statuses)
        durations = [value for _, value in samples if isinstance(value, int | float) and value >= 0]
        success_rate = (success_count / sample_size) if sample_size > 0 else None
        avg_duration = (sum(durations) / len(durations)) if durations else None

        return {
            "event_name": event_name,
            "enabled": config.enabled,
            "expression": config.expression,
            "focus": config.focus,
            "next_run": next_run,
            "success_rate": success_rate,
            "average_duration_seconds": avg_duration,
            "sample_size": sample_size,
        }

    # ------------------------------------------------------------------
    # Task 3.1 — Hatchet registration
    # ------------------------------------------------------------------

    def start(
        self,
        hatchet_client: HatchetClient,
        *,
        agent_runtime: AgentRuntime | None = None,
        ledger: Ledger | None = None,
        tenant_id: str = "default",
    ) -> None:
        """Register all stored triggers as Hatchet cron tasks.

        Must be called after *hatchet_client* is connected (i.e. after
        ``HatchetClient.connect()``).  Each trigger is wrapped in a closure
        that captures *config*, *agent_runtime*, *ledger*, and *tenant_id*.

        Args:
            hatchet_client: Connected :class:`HatchetClient` instance.
            agent_runtime: Optional :class:`AgentRuntime`; if omitted, all
                runs fall back to the configured fallback handler.
            ledger: Optional :class:`Ledger` for execution recording and
                governance constraint queries.
            tenant_id: Multi-tenancy identifier forwarded to runs.
        """
        tenant_id = self._normalize_tenant_id(tenant_id)
        self._hatchet_client = hatchet_client
        self._ledger = ledger
        self._tenant_id = tenant_id
        for config in self._triggers.values():
            self._register_hatchet_task(
                hatchet_client, config, agent_runtime, ledger, tenant_id
            )
        if agent_runtime is not None:
            self._register_agent_scheduled_run(hatchet_client, agent_runtime, tenant_id)
        logger.info(
            "Registered %d cron triggers with Hatchet", len(self._triggers)
        )

    def _register_hatchet_task(
        self,
        hatchet_client: HatchetClient,
        config: CronTriggerConfig,
        agent_runtime: AgentRuntime | None,
        ledger: Ledger | None,
        tenant_id: str,
    ) -> None:
        """Create the Hatchet task function for a single trigger."""
        registry = self

        async def cron_handler(_ctx: Any) -> dict[str, Any]:
            return await registry._run_cron(
                config, agent_runtime, ledger, tenant_id
            )

        cron_handler.__name__ = f"cron_{config.event_name}"
        # Apply the Hatchet task decorator
        hatchet_client.task(
            name=f"cron_{config.event_name}",
            cron=config.expression,
            retries=config.max_retries if config.retry_on_failure else 0,
            priority=config.priority or 1,
        )(cron_handler)
        self._hatchet_workflows[config.event_name] = cron_handler

    def _register_agent_scheduled_run(
        self,
        hatchet_client: HatchetClient,
        agent_runtime: AgentRuntime,
        tenant_id: str,
    ) -> None:
        """Register agent_scheduled_run task for schedule_once built-in tool."""

        async def agent_scheduled_run_handler(inp: Any, _ctx: Any) -> dict[str, Any]:
            data = inp if isinstance(inp, dict) else {}
            focus = data.get("focus", "")
            payload = {**data, "tenant_id": data.get("tenant_id", tenant_id)}
            result = await agent_runtime.trigger_event(
                "scheduled_run",
                focus=focus or None,
                payload=payload,
                tenant_id=payload.get("tenant_id", tenant_id),
            )
            return {"status": "success", "run_id": result.get("run_id")}

        agent_scheduled_run_handler.__name__ = "agent_scheduled_run"
        hatchet_client.task(
            name="agent_scheduled_run",
            retries=1,
        )(agent_scheduled_run_handler)
        self._hatchet_workflows["agent_scheduled_run"] = agent_scheduled_run_handler

    # ------------------------------------------------------------------
    # Task 3.2 — Main execution step
    # ------------------------------------------------------------------

    async def _run_cron(
        self,
        config: CronTriggerConfig,
        agent_runtime: AgentRuntime | None,
        ledger: Ledger | None,
        tenant_id: str,
    ) -> dict[str, Any]:
        """Execute a single cron trigger run (called by the Hatchet step)."""
        tenant_id = self._normalize_tenant_id(tenant_id)
        execution = CronExecution(
            execution_id=str(uuid.uuid4()),
            event_name=config.event_name,
            triggered_at=datetime.now(timezone.utc),
            status=ExecutionStatus.PENDING,
            context={
                "trigger_type": "cron",
                "expression": config.expression,
                "focus": config.focus,
            },
        )
        slot_acquired = await self._acquire_run_slot(tenant_id, config.event_name)
        if not slot_acquired:
            return {
                "status": ExecutionStatus.SKIPPED.value,
                "reason": "cron run already in progress",
            }
        self._cron_logger.log_trigger(config.event_name, execution.context)
        self._active_tasks += 1
        self._metrics.set_active_tasks(self._active_tasks)

        try:
            # Skip if disabled
            if not config.enabled:
                execution.status = ExecutionStatus.SKIPPED
                execution.skip_reason = "trigger disabled"
                return {"status": execution.status.value, "reason": execution.skip_reason}

            # Task 3.3 — governance checks
            passed, reason = await self._check_governance(
                config, execution, ledger, tenant_id
            )
            if not passed:
                execution.status = ExecutionStatus.SKIPPED
                execution.skip_reason = reason
                self._cron_logger.log_governance_skip(config.event_name, reason)
                return {"status": execution.status.value, "reason": reason}

            # Task 3.4 — Agent vs Fallback decision
            use_agent = self._should_use_agent(config)
            execution.decision_mode = "agent" if use_agent else "fallback"
            execution.status = ExecutionStatus.RUNNING
            execution.started_at = datetime.now(timezone.utc)
            self._cron_logger.log_execution_start(
                config.event_name,
                execution.execution_id,
                execution.decision_mode,
            )

            if use_agent and agent_runtime is not None:
                # Task 3.5 — Agent path
                await self._execute_agent(config, execution, agent_runtime)
            else:
                # Task 3.6 — Fallback path
                await self._execute_fallback(config, execution)

            if execution.status == ExecutionStatus.RUNNING:
                execution.status = ExecutionStatus.SUCCESS

        except Exception as exc:
            execution.status = ExecutionStatus.FAILED
            execution.error_message = str(exc)
            execution.error_traceback = _traceback.format_exc()
            self._cron_logger.log_execution_failed(
                config.event_name,
                execution.execution_id,
                execution.error_message,
            )
            # Task 3.7 — failure handling
            await self._handle_failure(config, execution)

        finally:
            try:
                execution.completed_at = datetime.now(timezone.utc)
                if execution.started_at is not None:
                    execution.duration_seconds = (
                        execution.completed_at - execution.started_at
                    ).total_seconds()
                self._record_recent_execution(
                    config.event_name,
                    execution.status.value,
                    execution.duration_seconds,
                )
                # Record run-level limit violations for auditing (config fields exist but are not pre-run enforced)
                if config.max_cost_per_run is not None and (execution.cost_usd or 0) > config.max_cost_per_run:
                    execution.governance_checks["max_cost_per_run_exceeded"] = True
                if config.max_duration is not None and (execution.duration_seconds or 0) > config.max_duration:
                    execution.governance_checks["max_duration_exceeded"] = True
                self._metrics.record_execution(config.event_name, execution)
                self._cron_logger.log_execution_complete(
                    config.event_name,
                    execution.execution_id,
                    execution.status.value,
                    execution.duration_seconds,
                )
                if ledger is not None:
                    agent_id = (self.app.name if self.app else None) or config.event_name
                    await self._governance.record_execution(
                        ledger, config, execution, tenant_id, agent_id=agent_id
                    )
                    await self._governance.update_circuit_breaker(config, ledger, tenant_id)
            finally:
                await self._release_run_slot(tenant_id, config.event_name)
                self._active_tasks = max(0, self._active_tasks - 1)
                self._metrics.set_active_tasks(self._active_tasks)

        return {
            "status": execution.status.value,
            "execution_id": execution.execution_id,
            "duration_seconds": execution.duration_seconds,
        }

    # ------------------------------------------------------------------
    # Task 3.3 — Governance checks
    # ------------------------------------------------------------------

    async def _check_governance(
        self,
        config: CronTriggerConfig,
        execution: CronExecution,
        ledger: Ledger | None,
        tenant_id: str,
    ) -> tuple[bool, str]:
        """Delegate governance checks to CronGovernance adapter."""
        return await self._governance.check_constraints(config, execution, ledger, tenant_id)

    async def _check_constraints_core(
        self,
        config: CronTriggerConfig,
        execution: CronExecution,
        ledger: Ledger | None,
        tenant_id: str,
    ) -> tuple[bool, str]:
        """Check governance constraints; return (passed, reason).

        Enforces: cooldown_seconds, max_daily_runs, max_daily_cost.
        max_cost_per_run and max_duration are not enforced here (run-level
        limits); violations are recorded in governance_checks after the run.
        Without a Ledger, time-based constraints are skipped (fail-open).
        """
        checks: dict[str, Any] = {}
        triggered_at = execution.triggered_at
        if triggered_at.tzinfo is None:
            triggered_at = triggered_at.replace(tzinfo=timezone.utc)
        execution_day = triggered_at.astimezone(timezone.utc).date()

        if ledger is not None and config.cooldown_seconds > 0:
            last = await self._get_last_successful_execution(
                ledger=ledger,
                tenant_id=tenant_id,
                event_name=config.event_name,
            )
            if last is not None:
                last_dt = last.created_at
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                elapsed = (
                    datetime.now(timezone.utc) - last_dt
                ).total_seconds()
                if elapsed < config.cooldown_seconds:
                    checks["cooldown"] = False
                    execution.governance_checks = checks
                    return (
                        False,
                        f"Cooldown not satisfied: {elapsed:.1f}s < {config.cooldown_seconds}s",
                    )
            checks["cooldown"] = True

        if ledger is not None and config.max_daily_runs is not None:
            today_runs = await self._count_today_executions(
                ledger=ledger,
                tenant_id=tenant_id,
                event_name=config.event_name,
                day=execution_day,
            )
            if today_runs >= config.max_daily_runs:
                checks["daily_runs"] = False
                execution.governance_checks = checks
                return (
                    False,
                    f"Daily run limit reached: {today_runs} >= {config.max_daily_runs}",
                )
            checks["daily_runs"] = True

        if ledger is not None and config.max_daily_cost is not None:
            today_cost = await self._sum_today_cost(
                ledger=ledger,
                tenant_id=tenant_id,
                event_name=config.event_name,
                day=execution_day,
            )
            if today_cost >= config.max_daily_cost:
                checks["daily_cost"] = False
                execution.governance_checks = checks
                return (
                    False,
                    f"Daily cost limit reached: ${today_cost:.4f} >= ${config.max_daily_cost}",
            )
            checks["daily_cost"] = True

        allowed, cb_reason = self._circuit_breaker.check(config.event_name)
        checks["circuit_breaker"] = allowed
        if not allowed:
            execution.governance_checks = checks
            return False, cb_reason

        execution.governance_checks = checks
        return True, ""

    async def _get_last_successful_execution(
        self,
        *,
        ledger: Ledger,
        tenant_id: str,
        event_name: str,
    ) -> Any | None:
        """Return latest successful execution record for an event."""
        from owlclaw.governance.ledger import LedgerQueryFilters

        records = await ledger.query_records(
            tenant_id,
            LedgerQueryFilters(
                capability_name=event_name,
                order_by="created_at DESC",
                limit=20,
            ),
        )
        for record in records:
            if str(getattr(record, "status", "")).strip().lower() in {"success", "fallback"}:
                return record
        return None

    async def _count_today_executions(
        self,
        *,
        ledger: Ledger,
        tenant_id: str,
        event_name: str,
        day: date,
    ) -> int:
        """Count today's executions for a trigger."""
        from owlclaw.governance.ledger import LedgerQueryFilters

        records = await ledger.query_records(
            tenant_id,
            LedgerQueryFilters(
                capability_name=event_name,
                start_date=day,
                end_date=day,
            ),
        )
        return len(records)

    async def _sum_today_cost(
        self,
        *,
        ledger: Ledger,
        tenant_id: str,
        event_name: str,
        day: date,
    ) -> float:
        """Sum today's estimated cost for a trigger."""
        from owlclaw.governance.ledger import LedgerQueryFilters

        records = await ledger.query_records(
            tenant_id,
            LedgerQueryFilters(
                capability_name=event_name,
                start_date=day,
                end_date=day,
            ),
        )
        return float(sum(float(getattr(record, "estimated_cost", 0) or 0) for record in records))

    async def _get_recent_executions(
        self,
        *,
        ledger: Ledger,
        tenant_id: str,
        event_name: str,
        limit: int = 10,
    ) -> list[Any]:
        """Return recent execution records ordered by newest first."""
        from owlclaw.governance.ledger import LedgerQueryFilters

        safe_limit = max(1, min(int(limit), 100))
        records = await ledger.query_records(
            tenant_id,
            LedgerQueryFilters(
                capability_name=event_name,
                order_by="created_at DESC",
                limit=safe_limit,
            ),
        )
        return list(records)

    # ------------------------------------------------------------------
    # Task 3.4 — Agent vs Fallback decision
    # ------------------------------------------------------------------

    @staticmethod
    def _should_use_agent(config: CronTriggerConfig) -> bool:
        """Return True if this run should use the Agent path.

        Uses ``migration_weight`` (0.0 → always fallback, 1.0 → always Agent).
        """
        return random.random() < config.migration_weight

    # ------------------------------------------------------------------
    # Task 3.5 — Agent execution path
    # ------------------------------------------------------------------

    async def _execute_agent(
        self,
        config: CronTriggerConfig,
        execution: CronExecution,
        agent_runtime: AgentRuntime,
    ) -> None:
        """Invoke agent_runtime.trigger_event and update execution record."""
        result = await agent_runtime.trigger_event(
            config.event_name,
            focus=config.focus,
            payload=execution.context,
        )
        if not isinstance(result, dict):
            raise RuntimeError("agent_runtime.trigger_event must return a dictionary")
        run_id = result.get("run_id")
        execution.agent_run_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else None
        raw_calls = result.get("tool_calls_total", 0)
        if isinstance(raw_calls, bool):
            execution.llm_calls = 0
        else:
            try:
                execution.llm_calls = max(0, int(raw_calls))
            except (TypeError, ValueError):
                execution.llm_calls = 0
        # Cost tracking requires Langfuse / litellm usage callback (future)

    # ------------------------------------------------------------------
    # Task 3.6 — Fallback execution path
    # ------------------------------------------------------------------

    async def _execute_fallback(
        self,
        config: CronTriggerConfig,
        execution: CronExecution,
    ) -> None:
        """Invoke the configured fallback handler (if any)."""
        if config.fallback_handler is None:
            logger.warning(
                "No fallback handler for '%s'; skipping fallback",
                config.event_name,
            )
            execution.status = ExecutionStatus.SKIPPED
            execution.skip_reason = "no fallback handler"
            return

        execution.decision_mode = "fallback"
        result = config.fallback_handler()
        if inspect.isawaitable(result):
            await result
        execution.status = ExecutionStatus.FALLBACK

    # ------------------------------------------------------------------
    # Task 3.7 — Failure handling
    # ------------------------------------------------------------------

    async def _handle_failure(
        self,
        config: CronTriggerConfig,
        execution: CronExecution,
    ) -> None:
        """Apply fallback_strategy on failure."""
        if config.fallback_strategy == "never":
            return
        if config.fallback_strategy in ("on_failure", "always"):
            try:
                await self._execute_fallback(config, execution)
            except Exception as fb_exc:
                logger.exception(
                    "Fallback handler also failed for '%s': %s",
                    config.event_name,
                    fb_exc,
                )

    # ------------------------------------------------------------------
    # Ledger recording helper
    # ------------------------------------------------------------------

    async def _record_to_ledger(
        self,
        ledger: Ledger,
        config: CronTriggerConfig,
        execution: CronExecution,
        tenant_id: str,
        *,
        agent_id: str,
    ) -> None:
        """Enqueue execution record to the Ledger (non-blocking).

        Uses app-level *agent_id* (e.g. OwlClaw app name) for cost and
        governance queries; run_id remains execution_id for traceability.
        """
        duration_ms = int((execution.duration_seconds or 0) * 1000)
        try:
            await ledger.record_execution(
                tenant_id=tenant_id,
                agent_id=agent_id,
                run_id=execution.execution_id,
                capability_name=config.event_name,
                task_type="cron_execution",
                input_params=execution.context,
                output_result={"agent_run_id": execution.agent_run_id},
                decision_reasoning=execution.skip_reason,
                execution_time_ms=duration_ms,
                llm_model="",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal(str(execution.cost_usd)),
                status=execution.status.value,
                error_message=execution.error_message,
            )
        except Exception as exc:
            logger.exception(
                "Failed to record execution for '%s': %s", config.event_name, exc
            )
