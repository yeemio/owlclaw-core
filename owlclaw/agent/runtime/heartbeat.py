"""HeartbeatChecker — check pending events to decide if LLM call is needed.

Heartbeat mechanism: when trigger is "heartbeat", no events => skip LLM, save cost.
Event sources implementation status:
- database: implemented (reads recent ledger statuses)
- schedule: implemented (checks due scheduled runs via injected scheduler client)
- webhook/queue/external_api: extension hooks only (config-gated; warn when enabled)
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_EVENT_SOURCES = ["webhook", "queue", "database", "schedule", "external_api"]
_SUPPORTED_EVENT_SOURCES = frozenset(_DEFAULT_EVENT_SOURCES)


class HeartbeatChecker:
    """Check for pending events to decide if LLM call is needed.

    When Heartbeat triggers an Agent run, this checker queries configured
    event sources. If no source reports events, the run can be skipped
    (no LLM call) to save cost.

    Args:
        agent_id: Stable identifier for the Agent.
        config: Heartbeat configuration. Keys:
            - event_sources: list of source names to check (default: webhook,
              queue, database, schedule, external_api)
            - enabled: if False, check_events() always returns False
    """

    def __init__(
        self,
        agent_id: str,
        config: dict[str, Any] | None = None,
        *,
        ledger: Any | None = None,
    ) -> None:
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id must be a non-empty string")
        self.agent_id = agent_id.strip()
        cfg = config or {}
        self._enabled = self._normalize_enabled(cfg.get("enabled", True))
        self._event_sources = self._normalize_event_sources(
            cfg.get("event_sources", _DEFAULT_EVENT_SOURCES)
        )
        self._ledger = ledger
        self._database_session_factory = cfg.get("database_session_factory")
        self._database_lookback_seconds = self._normalize_positive_number(
            cfg.get("database_lookback_seconds", cfg.get("database_lookback_minutes", 5) * 60),
            default=300.0,
        )
        self._database_query_timeout_seconds = self._normalize_positive_number(
            cfg.get("database_query_timeout_seconds", 0.5),
            default=0.5,
        )
        self._database_min_interval_ms = self._normalize_positive_number(
            cfg.get("database_min_interval_ms", os.getenv("OWLCLAW_HEARTBEAT_MIN_DB_INTERVAL_MS", "500")),
            default=500.0,
        )
        self._database_latency_warn_ms = self._normalize_positive_number(
            cfg.get("database_latency_warn_ms", 500),
            default=500.0,
        )
        self._webhook_enabled = self._normalize_enabled(cfg.get("webhook_enabled", False))
        self._queue_enabled = self._normalize_enabled(cfg.get("queue_enabled", False))
        self._schedule_enabled = self._normalize_enabled(cfg.get("schedule_enabled", True))
        self._external_api_enabled = self._normalize_enabled(cfg.get("external_api_enabled", False))
        self._schedule_client = cfg.get("schedule_client", cfg.get("hatchet_client"))
        self._schedule_due_window_seconds = self._normalize_positive_number(
            cfg.get("schedule_due_window_seconds", 0),
            default=0.0,
        )
        self._database_pending_statuses = self._normalize_pending_statuses(
            cfg.get("database_pending_statuses", ["error", "timeout", "pending"]),
        )
        self._last_database_check_monotonic: float | None = None
        self._last_database_check_result: bool = False

    @staticmethod
    def _normalize_enabled(raw_enabled: Any) -> bool:
        """Normalize enabled flag from bool/string values."""
        if isinstance(raw_enabled, bool):
            return raw_enabled
        if isinstance(raw_enabled, str):
            value = raw_enabled.strip().lower()
            if value in {"false", "0", "no", "off"}:
                return False
            if value in {"true", "1", "yes", "on"}:
                return True
        return bool(raw_enabled)

    @staticmethod
    def _normalize_event_sources(raw_sources: Any) -> list[str]:
        """Normalize event_sources config into a deduplicated string list."""
        if isinstance(raw_sources, str):
            items = [raw_sources]
        elif isinstance(raw_sources, list | tuple | set):
            if len(raw_sources) == 0:
                return []
            items = list(raw_sources)
        else:
            return list(_DEFAULT_EVENT_SOURCES)

        normalized: list[str] = []
        for item in items:
            if not isinstance(item, str):
                continue
            source = item.strip().lower()
            if source and source in _SUPPORTED_EVENT_SOURCES and source not in normalized:
                normalized.append(source)
        return normalized if normalized else list(_DEFAULT_EVENT_SOURCES)

    @staticmethod
    def _normalize_positive_number(raw: Any, *, default: float) -> float:
        if isinstance(raw, bool):
            return default
        if isinstance(raw, int | float):
            value = float(raw)
            return value if value >= 0 else default
        if isinstance(raw, str):
            value = raw.strip()
            if not value:
                return default
            try:
                parsed = float(value)
            except ValueError:
                return default
            return parsed if parsed >= 0 else default
        return default

    @staticmethod
    def _normalize_pending_statuses(raw: Any) -> tuple[str, ...]:
        if isinstance(raw, str):
            items: list[Any] = [raw]
        elif isinstance(raw, list | tuple | set):
            items = list(raw)
        else:
            items = ["error", "timeout", "pending"]
        normalized: list[str] = []
        for item in items:
            if not isinstance(item, str):
                continue
            status = item.strip().lower()
            if status and status not in normalized:
                normalized.append(status)
        return tuple(normalized or ["error", "timeout", "pending"])

    async def check_events(self, tenant_id: str = "default") -> bool:
        """Check if there are pending events in any configured source.

        Returns:
            True if any source has events, False otherwise.
            When disabled, always returns False (no events).
        """
        if not self._enabled:
            logger.debug(
                "HeartbeatChecker disabled agent_id=%s, assuming no events",
                self.agent_id,
            )
            return False

        for source in self._event_sources:
            try:
                if await self._check_source(source, tenant_id=tenant_id):
                    logger.info(
                        "HeartbeatChecker found events agent_id=%s source=%s tenant_id=%s",
                        self.agent_id,
                        source,
                        tenant_id,
                    )
                    return True
            except Exception as e:
                logger.warning(
                    "HeartbeatChecker error checking source=%s agent_id=%s tenant_id=%s: %s",
                    source,
                    self.agent_id,
                    tenant_id,
                    e,
                    exc_info=True,
                )
        return False

    async def _check_source(self, source: str, *, tenant_id: str) -> bool:
        """Check a specific event source. Returns True if events exist."""
        if source == "webhook":
            return await self._check_webhook_events()
        if source == "queue":
            return await self._check_queue_events()
        if source == "database":
            return await self._check_database_events(tenant_id=tenant_id)
        if source == "schedule":
            return await self._check_schedule_events()
        if source == "external_api":
            return await self._check_external_api_events()
        logger.warning("HeartbeatChecker unknown source=%s", source)
        return False

    async def _check_webhook_events(self) -> bool:
        """Check for new webhook events (extension hook; not implemented)."""
        if self._webhook_enabled:
            logger.warning(
                "HeartbeatChecker webhook source enabled but no implementation agent_id=%s",
                self.agent_id,
            )
        return False

    async def _check_queue_events(self) -> bool:
        """Check for new queue messages (extension hook; not implemented)."""
        if self._queue_enabled:
            logger.warning(
                "HeartbeatChecker queue source enabled but no implementation agent_id=%s",
                self.agent_id,
            )
        return False

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return None
            if candidate.endswith("Z"):
                candidate = f"{candidate[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(candidate)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        return None

    def _resolve_schedule_client(self) -> Any | None:
        configured = self._schedule_client
        if configured is None:
            return None
        if callable(configured):
            try:
                candidate = configured()
                return candidate if candidate is not None else configured
            except TypeError:
                return configured
            except Exception:
                logger.warning("HeartbeatChecker schedule client factory failed", exc_info=True)
                return None
        return configured

    async def _list_scheduled_tasks(self, client: Any) -> list[Any]:
        list_fn = getattr(client, "list_scheduled_tasks", None)
        if callable(list_fn):
            try:
                result = list_fn(agent_id=self.agent_id)
            except TypeError:
                result = list_fn()
        elif callable(client):
            try:
                result = client(agent_id=self.agent_id)
            except TypeError:
                result = client()
        else:
            return []
        if inspect.isawaitable(result):
            result = await result
        return list(result) if isinstance(result, list | tuple) else []

    def _schedule_task_is_due(self, task: Any, *, now: datetime) -> bool:
        due_raw: Any
        if isinstance(task, dict):
            due_raw = (
                task.get("due_at")
                or task.get("run_at")
                or task.get("scheduled_at")
                or task.get("next_run_at")
                or task.get("eta")
            )
        else:
            due_raw = (
                getattr(task, "due_at", None)
                or getattr(task, "run_at", None)
                or getattr(task, "scheduled_at", None)
                or getattr(task, "next_run_at", None)
                or getattr(task, "eta", None)
            )
        due_at = self._coerce_datetime(due_raw)
        if due_at is None:
            # Queued/scheduled entry without timestamp is treated as actionable.
            return True
        return due_at <= (now + timedelta(seconds=self._schedule_due_window_seconds))

    def _resolve_database_session_factory(self) -> Any | None:
        configured = self._database_session_factory
        if callable(configured):
            return configured
        if self._ledger is None:
            return None
        # Use Ledger's public API only (no access to private _session_factory).
        if not hasattr(self._ledger, "get_readonly_session_factory"):
            return None
        get_factory = self._ledger.get_readonly_session_factory
        if not callable(get_factory):
            return None
        try:
            candidate = get_factory()
        except Exception:
            logger.warning(
                "HeartbeatChecker ledger readonly session factory resolution failed agent_id=%s",
                self.agent_id,
                exc_info=True,
            )
            return None
        return candidate if callable(candidate) else None

    async def _check_database_events(self, *, tenant_id: str) -> bool:
        """Check pending events via read-only ledger table query."""
        session_factory = self._resolve_database_session_factory()
        if session_factory is None:
            return False
        now = monotonic()
        if self._database_min_interval_ms > 0 and self._last_database_check_monotonic is not None:
            min_interval_seconds = self._database_min_interval_ms / 1000.0
            if now - self._last_database_check_monotonic < min_interval_seconds:
                return self._last_database_check_result

        from sqlalchemy import select

        from owlclaw.governance.ledger import LedgerRecord

        started = now
        window_start = datetime.now(timezone.utc) - timedelta(seconds=self._database_lookback_seconds)

        async def _query_recent_pending() -> bool:
            async with session_factory() as session:
                stmt = (
                    select(LedgerRecord.id)
                    .where(LedgerRecord.tenant_id == tenant_id)
                    .where(LedgerRecord.agent_id == self.agent_id)
                    .where(LedgerRecord.status.in_(self._database_pending_statuses))
                    .where(LedgerRecord.created_at >= window_start)
                    .order_by(LedgerRecord.created_at.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                return result.scalar_one_or_none() is not None

        try:
            has_events = await asyncio.wait_for(
                _query_recent_pending(),
                timeout=self._database_query_timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._last_database_check_monotonic = monotonic()
            self._last_database_check_result = False
            logger.warning(
                "HeartbeatChecker database check timed out agent_id=%s tenant_id=%s timeout=%ss",
                self.agent_id,
                tenant_id,
                self._database_query_timeout_seconds,
            )
            return False
        except Exception:
            self._last_database_check_monotonic = monotonic()
            self._last_database_check_result = False
            logger.warning(
                "HeartbeatChecker database check failed agent_id=%s tenant_id=%s",
                self.agent_id,
                tenant_id,
                exc_info=True,
            )
            return False

        self._last_database_check_monotonic = monotonic()
        self._last_database_check_result = has_events
        elapsed_ms = (monotonic() - started) * 1000
        if elapsed_ms > self._database_latency_warn_ms:
            logger.warning(
                "HeartbeatChecker database check slow agent_id=%s tenant_id=%s elapsed_ms=%.2f",
                self.agent_id,
                tenant_id,
                elapsed_ms,
            )
        return has_events

    async def _check_schedule_events(self) -> bool:
        """Check due scheduled tasks from scheduler integration client."""
        if not self._schedule_enabled:
            return False
        client = self._resolve_schedule_client()
        if client is None:
            return False
        try:
            tasks = await self._list_scheduled_tasks(client)
        except Exception:
            logger.warning(
                "HeartbeatChecker schedule check failed agent_id=%s",
                self.agent_id,
                exc_info=True,
            )
            return False
        if not tasks:
            return False
        now = datetime.now(timezone.utc)
        return any(self._schedule_task_is_due(task, now=now) for task in tasks)

    async def _check_external_api_events(self) -> bool:
        """Check external API notifications (extension hook; not implemented)."""
        if self._external_api_enabled:
            logger.warning(
                "HeartbeatChecker external_api source enabled but no implementation agent_id=%s",
                self.agent_id,
            )
        return False
