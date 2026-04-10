"""Built-in tools available to all Agents via LLM function calling."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from owlclaw.security.audit import SecurityAuditLog
from owlclaw.security.sanitizer import InputSanitizer
from owlclaw.triggers.cron import CronTriggerRegistry

if TYPE_CHECKING:
    from owlclaw.capabilities.registry import CapabilityRegistry
    from owlclaw.governance.ledger import Ledger
    from owlclaw.integrations.hatchet import HatchetClient

logger = logging.getLogger(__name__)

_SCHEDULED_RUN_TASK = "agent_scheduled_run"
_BUILTIN_TOOL_NAMES = frozenset(
    {
        "query_state",
        "log_decision",
        "schedule_once",
        "schedule_cron",
        "cancel_schedule",
        "remember",
        "recall",
    }
)
_SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")
_STATE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_:-]{1,128}$")


@dataclass
class BuiltInToolsContext:
    """Context passed to BuiltInTools.execute()."""

    agent_id: str
    run_id: str
    tenant_id: str = "default"

    def __post_init__(self) -> None:
        for field_name in ("agent_id", "run_id", "tenant_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            setattr(self, field_name, value.strip())


def _query_state_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "query_state",
            "description": "Query the current state from a registered state provider. Use this to get business context (e.g. market_state, portfolio_snapshot) before making decisions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "state_name": {
                        "type": "string",
                        "description": "Name of the state to query (must be registered via @app.state)",
                    },
                },
                "required": ["state_name"],
            },
        },
    }


def _log_decision_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "log_decision",
            "description": "Record a decision and reasoning to the audit ledger. Use when you choose no_action, defer, or want to document your reasoning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Brief explanation of the decision (max 1000 chars)",
                    },
                    "decision_type": {
                        "type": "string",
                        "enum": ["capability_selection", "schedule_decision", "no_action", "other"],
                        "description": "Type of decision",
                    },
                },
                "required": ["reasoning"],
            },
        },
    }


def _schedule_once_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "schedule_once",
            "description": (
                "Schedule a one-time delayed Agent run. "
                "Use when you need to check something later or wait for an event."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "delay_seconds": {
                        "type": "integer",
                        "description": "Delay in seconds (1–2592000, max 30 days)",
                        "minimum": 1,
                        "maximum": 2592000,
                    },
                    "focus": {
                        "type": "string",
                        "description": "What to focus on in the next run (e.g. 'check entry opportunities')",
                    },
                },
                "required": ["delay_seconds", "focus"],
            },
        },
    }


def _schedule_cron_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "schedule_cron",
            "description": (
                "Schedule a recurring Agent run using a cron expression. "
                "Use for periodic checks (e.g. every hour during trading hours)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cron_expression": {
                        "type": "string",
                        "description": (
                            "Cron expression (minute hour day month weekday), "
                            "e.g. '0 9 * * 1-5' for 9am on weekdays"
                        ),
                    },
                    "focus": {
                        "type": "string",
                        "description": "What to focus on in each run",
                    },
                },
                "required": ["cron_expression", "focus"],
            },
        },
    }


def _cancel_schedule_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "cancel_schedule",
            "description": "Cancel a scheduled run by schedule_id (from schedule_once or schedule_cron).",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {
                        "type": "string",
                        "description": "The ID returned by schedule_once",
                    },
                },
                "required": ["schedule_id"],
            },
        },
    }


def _remember_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store durable memory for future Agent runs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Memory content to store (1-2000 chars)",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags for later recall filtering",
                    },
                },
                "required": ["content"],
            },
        },
    }


def _recall_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search previously stored memories by query and optional tags.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query for relevant memories",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Maximum number of memories to return (default 5)",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tag filter",
                    },
                },
                "required": ["query"],
            },
        },
    }


class BuiltInTools:
    """Built-in tools: query_state, log_decision, schedule_once, cancel_schedule.

    Optional dependencies: capability_registry (query_state), ledger (log_decision),
    hatchet_client (schedule_once, cancel_schedule). If a dependency is None,
    the corresponding tool returns an error message.

    Error behavior:
    - ``raise_errors=False`` (default): return ``{"error": "<message>"}`` payloads.
    - ``raise_errors=True``: convert tool error payloads into typed exceptions
      (``ValueError``/``RuntimeError``/``TimeoutError``) for strict callers.
    """

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistry | None = None,
        ledger: Ledger | None = None,
        hatchet_client: HatchetClient | None = None,
        memory_system: Any | None = None,
        scheduled_run_task_name: str = _SCHEDULED_RUN_TASK,
        timeout_seconds: float = 30,
        max_calls_per_run: int = 50,
        max_schedule_calls_per_run: int = 20,
        raise_errors: bool = False,
        enforce_schedule_ownership: bool = False,
        remember_write_background: bool = False,
    ) -> None:
        self._registry = capability_registry
        self._ledger = ledger
        self._hatchet = hatchet_client
        self._memory = memory_system
        task_name = self._non_empty_str(scheduled_run_task_name)
        if task_name is None:
            raise ValueError("scheduled_run_task_name must be a non-empty string")
        self._scheduled_run_task = task_name
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int | float):
            raise ValueError("timeout_seconds must be a positive finite number")
        timeout_val = float(timeout_seconds)
        if not math.isfinite(timeout_val) or timeout_val <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")
        self._timeout = timeout_val
        if isinstance(max_calls_per_run, bool) or not isinstance(max_calls_per_run, int) or max_calls_per_run < 1:
            raise ValueError("max_calls_per_run must be a positive integer")
        self._max_calls_per_run = max_calls_per_run
        self._run_call_counts: dict[str, int] = {}
        self._run_call_locks: dict[str, asyncio.Lock] = {}
        if (
            isinstance(max_schedule_calls_per_run, bool)
            or not isinstance(max_schedule_calls_per_run, int)
            or max_schedule_calls_per_run < 1
        ):
            raise ValueError("max_schedule_calls_per_run must be a positive integer")
        self._max_schedule_calls_per_run = max_schedule_calls_per_run
        self._run_schedule_call_counts: dict[str, int] = {}
        self._schedule_owners: dict[str, str] = {}
        if not isinstance(raise_errors, bool):
            raise ValueError("raise_errors must be a boolean")
        self._raise_errors = raise_errors
        if not isinstance(enforce_schedule_ownership, bool):
            raise ValueError("enforce_schedule_ownership must be a boolean")
        self._enforce_schedule_ownership = enforce_schedule_ownership
        if not isinstance(remember_write_background, bool):
            raise ValueError("remember_write_background must be a boolean")
        self._remember_write_background = remember_write_background
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._sanitizer = InputSanitizer()
        self._security_audit = SecurityAuditLog()

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI-style function schemas for all built-in tools."""
        schemas = [
            _query_state_schema(),
            _log_decision_schema(),
            _schedule_once_schema(),
            _schedule_cron_schema(),
            _cancel_schedule_schema(),
            _remember_schema(),
            _recall_schema(),
        ]
        return schemas

    def is_builtin(self, tool_name: str) -> bool:
        """Return True if *tool_name* is a built-in tool."""
        if not isinstance(tool_name, str):
            return False
        return tool_name.strip() in _BUILTIN_TOOL_NAMES

    @staticmethod
    def _non_empty_str(value: Any) -> str | None:
        """Return trimmed non-empty string, else None."""
        if not isinstance(value, str):
            return None
        trimmed = value.strip()
        return trimmed if trimmed else None

    @staticmethod
    def _safe_name(value: str) -> str:
        normalized = _SAFE_NAME_PATTERN.sub("_", value.strip())
        normalized = normalized.strip("_")
        return normalized or "agent"

    def _format_internal_error(self, exc: Exception) -> str:
        """Return safe error payload for runtime failures.

        In strict mode we keep details for typed exception conversion in callers.
        In default mode we avoid exposing internal exception text.
        """
        if self._raise_errors:
            return str(exc)
        return "Tool execution failed due to an internal error."

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: BuiltInToolsContext,
    ) -> Any:
        """Execute a built-in tool by name.

        Raises:
            ValueError: If tool_name is not a built-in tool.
        """
        normalized_tool_name = tool_name.strip() if isinstance(tool_name, str) else ""
        if normalized_tool_name not in _BUILTIN_TOOL_NAMES:
            raise ValueError(f"Unknown built-in tool: {tool_name}")

        call_limit_error = await self._consume_run_call_budget(
            run_id=context.run_id,
            tool_name=normalized_tool_name,
            context=context,
        )
        if call_limit_error is not None:
            return {"error": call_limit_error}

        if not isinstance(arguments, dict):
            error = (
                f"Invalid arguments for built-in tool '{normalized_tool_name}': "
                "arguments must be a JSON object"
            )
            out = {
                "error": (
                    error
                )
            }
            await self._record_validation_failure(
                tool_name=normalized_tool_name,
                context=context,
                input_params={"arguments_type": type(arguments).__name__},
                error_message=error,
            )
            return out

        if normalized_tool_name == "query_state":
            result = await self._query_state(arguments, context)
            return self._coerce_error_result(result, normalized_tool_name)
        if normalized_tool_name == "log_decision":
            result = await self._log_decision(arguments, context)
            return self._coerce_error_result(result, normalized_tool_name)
        if normalized_tool_name == "schedule_once":
            result = await self._schedule_once(arguments, context)
            return self._coerce_error_result(result, normalized_tool_name)
        if normalized_tool_name == "schedule_cron":
            result = await self._schedule_cron(arguments, context)
            return self._coerce_error_result(result, normalized_tool_name)
        if normalized_tool_name == "cancel_schedule":
            result = await self._cancel_schedule(arguments, context)
            return self._coerce_error_result(result, normalized_tool_name)
        if normalized_tool_name == "remember":
            result = await self._remember(arguments, context)
            return self._coerce_error_result(result, normalized_tool_name)
        if normalized_tool_name == "recall":
            result = await self._recall(arguments, context)
            return self._coerce_error_result(result, normalized_tool_name)
        raise ValueError(f"Unknown built-in tool: {normalized_tool_name}")

    def _coerce_error_result(self, result: Any, tool_name: str) -> Any:
        """Optionally convert tool error payloads into typed exceptions."""
        if not self._raise_errors:
            return result
        if not isinstance(result, dict):
            return result
        error_message = result.get("error")
        if not isinstance(error_message, str) or not error_message.strip():
            return result
        normalized_error = error_message.strip()
        lower_error = normalized_error.lower()
        if "timed out" in lower_error:
            raise TimeoutError(normalized_error)
        validation_markers = (
            "required",
            "must be",
            "invalid ",
            "unknown built-in tool",
            "not configured",
            "limit exceeded",
        )
        if any(marker in lower_error for marker in validation_markers):
            raise ValueError(normalized_error)
        raise RuntimeError(f"{tool_name} failed: {normalized_error}")

    def reset_run_call_budget(self, run_id: str) -> None:
        """Clear call counters for a completed Agent run."""
        normalized_run_id = self._non_empty_str(run_id)
        if normalized_run_id is None:
            raise ValueError("run_id must be a non-empty string")
        self._run_call_counts.pop(normalized_run_id, None)
        self._run_call_locks.pop(normalized_run_id, None)
        self._run_schedule_call_counts.pop(normalized_run_id, None)

    def list_security_events(self) -> list[Any]:
        """Return security audit events captured by built-in tools."""
        return self._security_audit.list_events()

    async def _consume_run_call_budget(
        self,
        *,
        run_id: str,
        tool_name: str,
        context: BuiltInToolsContext,
    ) -> str | None:
        lock = self._run_call_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            current_count = self._run_call_counts.get(run_id, 0)
            next_count = current_count + 1
            if next_count > self._max_calls_per_run:
                error = (
                    f"Tool call limit exceeded for run '{run_id}': "
                    f"max_calls_per_run={self._max_calls_per_run}"
                )
                await self._record_validation_failure(
                    tool_name=tool_name,
                    context=context,
                    input_params={"run_id": run_id, "max_calls_per_run": self._max_calls_per_run},
                    error_message=error,
                )
                return error
            self._run_call_counts[run_id] = next_count
        return None

    async def _consume_schedule_call_budget(
        self,
        *,
        run_id: str,
        context: BuiltInToolsContext,
        tool_name: str,
    ) -> str | None:
        lock = self._run_call_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            current_count = self._run_schedule_call_counts.get(run_id, 0)
            next_count = current_count + 1
            if next_count > self._max_schedule_calls_per_run:
                error = (
                    f"Schedule frequency limit exceeded for run '{run_id}': "
                    f"max_schedule_calls_per_run={self._max_schedule_calls_per_run}"
                )
                self._security_audit.record(
                    event_type="schedule_rate_limited",
                    source=tool_name,
                    details={
                        "agent_id": context.agent_id,
                        "run_id": run_id,
                        "tenant_id": context.tenant_id,
                        "limit": self._max_schedule_calls_per_run,
                    },
                )
                await self._record_validation_failure(
                    tool_name=tool_name,
                    context=context,
                    input_params={
                        "run_id": run_id,
                        "max_schedule_calls_per_run": self._max_schedule_calls_per_run,
                    },
                    error_message=error,
                )
                return error
            self._run_schedule_call_counts[run_id] = next_count
        return None

    @staticmethod
    def _normalize_tags(raw_tags: Any) -> list[str] | None:
        if raw_tags is None:
            return []
        if not isinstance(raw_tags, list):
            return None
        tags: list[str] = []
        for raw in raw_tags:
            if not isinstance(raw, str):
                return None
            normalized = raw.strip().lower()
            if normalized and normalized not in tags:
                tags.append(normalized)
        return tags

    async def _call_memory_method(self, method_name: str, **kwargs: Any) -> Any:
        method = getattr(self._memory, method_name, None)
        if method is None:
            raise RuntimeError(f"memory system does not support '{method_name}'")
        result = method(**kwargs)
        if inspect.isawaitable(result):
            return await asyncio.wait_for(result, timeout=self._timeout)
        return result

    async def _query_state(
        self,
        arguments: dict[str, Any],
        context: BuiltInToolsContext,
    ) -> dict[str, Any]:
        state_name = self._non_empty_str(arguments.get("state_name"))
        if state_name is None:
            error = "state_name is required and must be a non-empty string"
            await self._record_validation_failure(
                tool_name="query_state",
                context=context,
                input_params={"state_name": arguments.get("state_name")},
                error_message=error,
            )
            return {"error": error}
        if _STATE_NAME_PATTERN.fullmatch(state_name) is None:
            error = "state_name contains invalid characters"
            await self._record_validation_failure(
                tool_name="query_state",
                context=context,
                input_params={"state_name": state_name},
                error_message=error,
            )
            self._security_audit.record(
                event_type="query_state_invalid_name",
                source="query_state",
                details={"agent_id": context.agent_id, "run_id": context.run_id, "state_name": state_name},
            )
            return {"error": error}

        if self._registry is None:
            error = "No capability registry configured; query_state unavailable"
            await self._record_validation_failure(
                tool_name="query_state",
                context=context,
                input_params={"state_name": state_name},
                error_message=error,
            )
            return {"error": error}

        start_ns = time.perf_counter_ns()
        try:
            result = await asyncio.wait_for(
                self._registry.get_state(state_name),
                timeout=self._timeout,
            )
            out = {"state": result}
            await self._record_tool_execution(
                tool_name="query_state",
                context=context,
                input_params={"state_name": state_name},
                output_result=out,
                start_ns=start_ns,
                status="success",
                error_message=None,
            )
            return out
        except asyncio.TimeoutError:
            error = f"query_state timed out after {self._timeout}s"
            await self._record_tool_execution(
                tool_name="query_state",
                context=context,
                input_params={"state_name": state_name},
                output_result=None,
                start_ns=start_ns,
                status="timeout",
                error_message=error,
            )
            return {"error": error}
        except ValueError as e:
            safe_error = self._format_internal_error(e)
            await self._record_tool_execution(
                tool_name="query_state",
                context=context,
                input_params={"state_name": state_name},
                output_result=None,
                start_ns=start_ns,
                status="error",
                error_message=e.__class__.__name__,
            )
            return {"error": safe_error}
        except Exception as e:
            logger.exception("query_state failed for %s", state_name)
            safe_error = self._format_internal_error(e)
            await self._record_tool_execution(
                tool_name="query_state",
                context=context,
                input_params={"state_name": state_name},
                output_result=None,
                start_ns=start_ns,
                status="error",
                error_message=e.__class__.__name__,
            )
            return {"error": safe_error}

    async def _log_decision(
        self,
        arguments: dict[str, Any],
        context: BuiltInToolsContext,
    ) -> dict[str, Any]:
        reasoning = self._non_empty_str(arguments.get("reasoning"))
        if reasoning is None:
            error = "reasoning is required and must be a non-empty string"
            await self._record_validation_failure(
                tool_name="log_decision",
                context=context,
                input_params={"reasoning": arguments.get("reasoning")},
                error_message=error,
            )
            return {"error": error}
        if len(reasoning) > 1000:
            error = "reasoning must not exceed 1000 characters"
            await self._record_validation_failure(
                tool_name="log_decision",
                context=context,
                input_params={"reasoning_length": len(reasoning)},
                error_message=error,
            )
            return {"error": error}

        decision_type = arguments.get("decision_type", "other")
        if decision_type not in ("capability_selection", "schedule_decision", "no_action", "other"):
            decision_type = "other"

        if self._ledger is None:
            return {"decision_id": "no-ledger", "logged": False}

        try:
            decision_id = f"decision-{uuid.uuid4().hex}"
            await self._ledger.record_execution(
                tenant_id=context.tenant_id,
                agent_id=context.agent_id,
                run_id=context.run_id,
                capability_name="log_decision",
                task_type="decision_log",
                input_params={"reasoning": reasoning, "decision_type": decision_type},
                output_result={"logged": True, "decision_id": decision_id},
                decision_reasoning=reasoning,
                execution_time_ms=0,
                llm_model="builtin",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status="success",
            )
            return {"decision_id": decision_id, "logged": True}
        except Exception as e:
            logger.exception("log_decision failed")
            return {"error": self._format_internal_error(e), "logged": False}

    async def _schedule_once(
        self,
        arguments: dict[str, Any],
        context: BuiltInToolsContext,
    ) -> dict[str, Any]:
        delay = arguments.get("delay_seconds")
        focus = self._non_empty_str(arguments.get("focus"))
        if isinstance(delay, bool) or not isinstance(delay, int) or delay < 1 or delay > 2592000:
            error = "delay_seconds must be an integer between 1 and 2592000"
            await self._record_validation_failure(
                tool_name="schedule_once",
                context=context,
                input_params={"delay_seconds": delay},
                error_message=error,
            )
            return {"error": error}
        if focus is None:
            error = "focus is required and must be a non-empty string"
            await self._record_validation_failure(
                tool_name="schedule_once",
                context=context,
                input_params={"focus": arguments.get("focus")},
                error_message=error,
            )
            return {"error": error}
        if self._hatchet is None:
            error = "Hatchet not configured; schedule_once unavailable"
            await self._record_validation_failure(
                tool_name="schedule_once",
                context=context,
                input_params={"delay_seconds": delay, "focus": focus},
                error_message=error,
            )
            return {"error": error}
        schedule_limit_error = await self._consume_schedule_call_budget(
            run_id=context.run_id,
            context=context,
            tool_name="schedule_once",
        )
        if schedule_limit_error is not None:
            return {"error": schedule_limit_error}
        start_ns = time.perf_counter_ns()
        try:
            schedule_id = await asyncio.wait_for(
                self._hatchet.schedule_task(
                    self._scheduled_run_task,
                    delay,
                    agent_id=context.agent_id,
                    trigger="schedule_once",
                    focus=focus,
                    scheduled_by_run_id=context.run_id,
                    tenant_id=context.tenant_id,
                ),
                timeout=self._timeout,
            )
            out = {
                "schedule_id": schedule_id,
                "scheduled_at": f"in {delay} seconds",
                "focus": focus,
            }
            if isinstance(schedule_id, str) and schedule_id.strip():
                self._schedule_owners[schedule_id] = context.agent_id
            await self._record_tool_execution(
                tool_name="schedule_once",
                context=context,
                input_params={"delay_seconds": delay, "focus": focus},
                output_result=out,
                start_ns=start_ns,
                status="success",
                error_message=None,
            )
            return out
        except asyncio.TimeoutError:
            error = f"schedule_once timed out after {self._timeout}s"
            await self._record_tool_execution(
                tool_name="schedule_once",
                context=context,
                input_params={"delay_seconds": delay, "focus": focus},
                output_result=None,
                start_ns=start_ns,
                status="timeout",
                error_message=error,
            )
            return {"error": error}
        except Exception as e:
            logger.exception("schedule_once failed")
            safe_error = self._format_internal_error(e)
            await self._record_tool_execution(
                tool_name="schedule_once",
                context=context,
                input_params={"delay_seconds": delay, "focus": focus},
                output_result=None,
                start_ns=start_ns,
                status="error",
                error_message=e.__class__.__name__,
            )
            return {"error": safe_error}

    def _validate_cron_expression(self, expr: str) -> bool:
        """Validate cron expression (5 fields)."""
        return CronTriggerRegistry._validate_cron_expression(expr)

    async def _schedule_cron(
        self,
        arguments: dict[str, Any],
        context: BuiltInToolsContext,
    ) -> dict[str, Any]:
        cron_expr = self._non_empty_str(arguments.get("cron_expression"))
        focus = self._non_empty_str(arguments.get("focus"))
        if cron_expr is None:
            error = "cron_expression is required and must be a non-empty string"
            await self._record_validation_failure(
                tool_name="schedule_cron",
                context=context,
                input_params={"cron_expression": arguments.get("cron_expression")},
                error_message=error,
            )
            return {"error": error}
        if focus is None:
            error = "focus is required and must be a non-empty string"
            await self._record_validation_failure(
                tool_name="schedule_cron",
                context=context,
                input_params={"focus": arguments.get("focus")},
                error_message=error,
            )
            return {"error": error}
        if not self._validate_cron_expression(cron_expr):
            error = f"Invalid cron expression: {cron_expr!r}"
            await self._record_validation_failure(
                tool_name="schedule_cron",
                context=context,
                input_params={"cron_expression": cron_expr, "focus": focus},
                error_message=error,
            )
            return {"error": error}
        if self._hatchet is None:
            error = "Hatchet not configured; schedule_cron unavailable"
            await self._record_validation_failure(
                tool_name="schedule_cron",
                context=context,
                input_params={"cron_expression": cron_expr, "focus": focus},
                error_message=error,
            )
            return {"error": error}
        schedule_limit_error = await self._consume_schedule_call_budget(
            run_id=context.run_id,
            context=context,
            tool_name="schedule_cron",
        )
        if schedule_limit_error is not None:
            return {"error": schedule_limit_error}
        safe_agent_id = self._safe_name(context.agent_id)
        cron_name = f"agent_cron_{safe_agent_id}_{uuid.uuid4().hex[:12]}"
        input_data = {
            "agent_id": context.agent_id,
            "trigger": "schedule_cron",
            "focus": focus,
            "scheduled_by_run_id": context.run_id,
            "tenant_id": context.tenant_id,
        }
        start_ns = time.perf_counter_ns()
        try:
            schedule_id = await asyncio.wait_for(
                self._hatchet.schedule_cron(
                    workflow_name=self._scheduled_run_task,
                    cron_name=cron_name,
                    expression=cron_expr,
                    input_data=input_data,
                ),
                timeout=self._timeout,
            )
            out = {
                "schedule_id": schedule_id,
                "cron_name": cron_name,
                "cron_expression": cron_expr,
                "focus": focus,
            }
            if isinstance(schedule_id, str) and schedule_id.strip():
                self._schedule_owners[schedule_id] = context.agent_id
            await self._record_tool_execution(
                tool_name="schedule_cron",
                context=context,
                input_params={"cron_expression": cron_expr, "focus": focus},
                output_result=out,
                start_ns=start_ns,
                status="success",
                error_message=None,
            )
            return out
        except asyncio.TimeoutError:
            error = f"schedule_cron timed out after {self._timeout}s"
            await self._record_tool_execution(
                tool_name="schedule_cron",
                context=context,
                input_params={"cron_expression": cron_expr, "focus": focus},
                output_result=None,
                start_ns=start_ns,
                status="timeout",
                error_message=error,
            )
            return {"error": error}
        except Exception as e:
            logger.exception("schedule_cron failed")
            safe_error = self._format_internal_error(e)
            await self._record_tool_execution(
                tool_name="schedule_cron",
                context=context,
                input_params={"cron_expression": cron_expr, "focus": focus},
                output_result=None,
                start_ns=start_ns,
                status="error",
                error_message=e.__class__.__name__,
            )
            return {"error": safe_error}

    async def _cancel_schedule(
        self,
        arguments: dict[str, Any],
        context: BuiltInToolsContext,
    ) -> dict[str, Any]:
        schedule_id = self._non_empty_str(arguments.get("schedule_id"))
        if schedule_id is None:
            error = "schedule_id is required and must be a non-empty string"
            await self._record_validation_failure(
                tool_name="cancel_schedule",
                context=context,
                input_params={"schedule_id": arguments.get("schedule_id")},
                error_message=error,
            )
            return {"error": error}
        if self._hatchet is None:
            error = "Hatchet not configured; cancel_schedule unavailable"
            await self._record_validation_failure(
                tool_name="cancel_schedule",
                context=context,
                input_params={"schedule_id": schedule_id},
                error_message=error,
            )
            return {"error": error}
        owner = self._schedule_owners.get(schedule_id)
        if self._enforce_schedule_ownership and owner is not None and owner != context.agent_id:
            error = "permission denied: schedule_id does not belong to current agent"
            self._security_audit.record(
                event_type="schedule_ownership_denied",
                source="cancel_schedule",
                details={
                    "agent_id": context.agent_id,
                    "run_id": context.run_id,
                    "schedule_id": schedule_id,
                    "owner": owner,
                },
            )
            await self._record_validation_failure(
                tool_name="cancel_schedule",
                context=context,
                input_params={"schedule_id": schedule_id, "owner": owner},
                error_message=error,
            )
            return {"error": error}
        start_ns = time.perf_counter_ns()
        try:
            ok = await asyncio.wait_for(
                self._hatchet.cancel_task(schedule_id),
                timeout=self._timeout,
            )
            if not ok and hasattr(self._hatchet, "cancel_cron"):
                ok = await asyncio.wait_for(
                    self._hatchet.cancel_cron(schedule_id),
                    timeout=self._timeout,
                )
            ok = bool(ok)
            out = {"cancelled": ok, "schedule_id": schedule_id}
            if ok:
                self._schedule_owners.pop(schedule_id, None)
            await self._record_tool_execution(
                tool_name="cancel_schedule",
                context=context,
                input_params={"schedule_id": schedule_id},
                output_result=out,
                start_ns=start_ns,
                status="success" if ok else "not_found",
                error_message=None if ok else "schedule not found",
            )
            return out
        except asyncio.TimeoutError:
            error = f"cancel_schedule timed out after {self._timeout}s"
            await self._record_tool_execution(
                tool_name="cancel_schedule",
                context=context,
                input_params={"schedule_id": schedule_id},
                output_result=None,
                start_ns=start_ns,
                status="timeout",
                error_message=error,
            )
            return {"error": error}
        except Exception as e:
            logger.exception("cancel_schedule failed")
            safe_error = self._format_internal_error(e)
            await self._record_tool_execution(
                tool_name="cancel_schedule",
                context=context,
                input_params={"schedule_id": schedule_id},
                output_result=None,
                start_ns=start_ns,
                status="error",
                error_message=e.__class__.__name__,
            )
            return {"error": safe_error}

    async def _remember(
        self,
        arguments: dict[str, Any],
        context: BuiltInToolsContext,
    ) -> dict[str, Any]:
        content = self._non_empty_str(arguments.get("content"))
        if content is None:
            error = "content is required and must be a non-empty string"
            await self._record_validation_failure(
                tool_name="remember",
                context=context,
                input_params={"content": arguments.get("content")},
                error_message=error,
            )
            return {"error": error}
        if len(content) > 2000:
            error = "content must not exceed 2000 characters"
            await self._record_validation_failure(
                tool_name="remember",
                context=context,
                input_params={"content_length": len(content)},
                error_message=error,
            )
            return {"error": error}
        sanitized = self._sanitizer.sanitize(content, source="remember")
        if sanitized.changed:
            self._security_audit.record(
                event_type="remember_content_sanitized",
                source="remember",
                details={
                    "agent_id": context.agent_id,
                    "run_id": context.run_id,
                    "tenant_id": context.tenant_id,
                    "modifications": sanitized.modifications,
                },
            )
            content = sanitized.sanitized.strip()
            if not content:
                error = "content was fully removed by sanitization"
                await self._record_validation_failure(
                    tool_name="remember",
                    context=context,
                    input_params={"modifications": sanitized.modifications},
                    error_message=error,
                )
                return {"error": error}
        tags = self._normalize_tags(arguments.get("tags"))
        if tags is None:
            error = "tags must be an array of strings"
            await self._record_validation_failure(
                tool_name="remember",
                context=context,
                input_params={"tags_type": type(arguments.get("tags")).__name__},
                error_message=error,
            )
            return {"error": error}
        if self._memory is None:
            error = "Memory system not configured; remember unavailable"
            await self._record_validation_failure(
                tool_name="remember",
                context=context,
                input_params={"tags": tags},
                error_message=error,
            )
            return {"error": error}

        start_ns = time.perf_counter_ns()
        try:
            if self._remember_write_background:
                queued_memory_id = f"memory-{uuid.uuid4().hex}"
                out = {"memory_id": queued_memory_id, "created_at": "", "tags": tags, "queued": True}
                task = asyncio.create_task(
                    self._call_memory_method("write", content=content, tags=tags)
                )
                self._background_tasks.add(task)
                task.add_done_callback(lambda t: self._background_tasks.discard(t))
                await self._record_tool_execution(
                    tool_name="remember",
                    context=context,
                    input_params={"content_length": len(content), "tags": tags, "queued": True},
                    output_result=out,
                    start_ns=start_ns,
                    status="success",
                    error_message=None,
                )
                return out
            payload = await self._call_memory_method("write", content=content, tags=tags)
            memory_id: str | None = None
            created_at: str | None = None
            if isinstance(payload, dict):
                raw_memory_id = payload.get("memory_id")
                raw_created_at = payload.get("created_at")
                if isinstance(raw_memory_id, str) and raw_memory_id.strip():
                    memory_id = raw_memory_id.strip()
                if isinstance(raw_created_at, str) and raw_created_at.strip():
                    created_at = raw_created_at.strip()
            memory_id = memory_id or f"memory-{uuid.uuid4().hex}"
            created_at = created_at or ""
            out = {"memory_id": memory_id, "created_at": created_at, "tags": tags}
            await self._record_tool_execution(
                tool_name="remember",
                context=context,
                input_params={"content_length": len(content), "tags": tags},
                output_result=out,
                start_ns=start_ns,
                status="success",
                error_message=None,
            )
            return out
        except asyncio.TimeoutError:
            error = f"remember timed out after {self._timeout}s"
            await self._record_tool_execution(
                tool_name="remember",
                context=context,
                input_params={"content_length": len(content), "tags": tags},
                output_result=None,
                start_ns=start_ns,
                status="timeout",
                error_message=error,
            )
            return {"error": error}
        except Exception as e:
            logger.exception("remember failed")
            safe_error = self._format_internal_error(e)
            await self._record_tool_execution(
                tool_name="remember",
                context=context,
                input_params={"content_length": len(content), "tags": tags},
                output_result=None,
                start_ns=start_ns,
                status="error",
                error_message=e.__class__.__name__,
            )
            return {"error": safe_error}

    async def _recall(
        self,
        arguments: dict[str, Any],
        context: BuiltInToolsContext,
    ) -> dict[str, Any]:
        query = self._non_empty_str(arguments.get("query"))
        if query is None:
            error = "query is required and must be a non-empty string"
            await self._record_validation_failure(
                tool_name="recall",
                context=context,
                input_params={"query": arguments.get("query")},
                error_message=error,
            )
            return {"error": error}
        raw_limit = arguments.get("limit", 5)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit < 1 or raw_limit > 20:
            error = "limit must be an integer between 1 and 20"
            await self._record_validation_failure(
                tool_name="recall",
                context=context,
                input_params={"limit": raw_limit},
                error_message=error,
            )
            return {"error": error}
        tags = self._normalize_tags(arguments.get("tags"))
        if tags is None:
            error = "tags must be an array of strings"
            await self._record_validation_failure(
                tool_name="recall",
                context=context,
                input_params={"tags_type": type(arguments.get("tags")).__name__},
                error_message=error,
            )
            return {"error": error}
        if self._memory is None:
            error = "Memory system not configured; recall unavailable"
            await self._record_validation_failure(
                tool_name="recall",
                context=context,
                input_params={"query": query, "limit": raw_limit, "tags": tags},
                error_message=error,
            )
            return {"error": error}

        start_ns = time.perf_counter_ns()
        try:
            result = await self._call_memory_method("search", query=query, limit=raw_limit, tags=tags)
            memories: list[Any] = result if isinstance(result, list) else []
            out = {"memories": memories, "count": len(memories)}
            await self._record_tool_execution(
                tool_name="recall",
                context=context,
                input_params={"query": query, "limit": raw_limit, "tags": tags},
                output_result={"count": len(memories)},
                start_ns=start_ns,
                status="success",
                error_message=None,
            )
            return out
        except asyncio.TimeoutError:
            error = f"recall timed out after {self._timeout}s"
            await self._record_tool_execution(
                tool_name="recall",
                context=context,
                input_params={"query": query, "limit": raw_limit, "tags": tags},
                output_result=None,
                start_ns=start_ns,
                status="timeout",
                error_message=error,
            )
            return {"error": error}
        except Exception as e:
            logger.exception("recall failed")
            safe_error = self._format_internal_error(e)
            await self._record_tool_execution(
                tool_name="recall",
                context=context,
                input_params={"query": query, "limit": raw_limit, "tags": tags},
                output_result=None,
                start_ns=start_ns,
                status="error",
                error_message=e.__class__.__name__,
            )
            return {"error": safe_error}

    async def _record_tool_execution(
        self,
        *,
        tool_name: str,
        context: BuiltInToolsContext,
        input_params: dict[str, Any],
        output_result: dict[str, Any] | None,
        start_ns: int,
        status: str,
        error_message: str | None,
    ) -> None:
        """Best-effort audit record for built-in tool execution."""
        if self._ledger is None:
            return
        try:
            elapsed_ms = max(0, (time.perf_counter_ns() - start_ns) // 1_000_000)
            await self._ledger.record_execution(
                tenant_id=context.tenant_id,
                agent_id=context.agent_id,
                run_id=context.run_id,
                capability_name=tool_name,
                task_type="builtin",
                input_params=input_params,
                output_result=output_result,
                decision_reasoning="builtin_tool_execution",
                execution_time_ms=elapsed_ms,
                llm_model="builtin",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status=status,
                error_message=error_message,
            )
        except Exception:
            logger.exception("Failed to record built-in tool execution: %s", tool_name)

    async def _record_validation_failure(
        self,
        *,
        tool_name: str,
        context: BuiltInToolsContext,
        input_params: dict[str, Any],
        error_message: str,
    ) -> None:
        """Record validation/availability errors for built-in tools."""
        await self._record_tool_execution(
            tool_name=tool_name,
            context=context,
            input_params=input_params,
            output_result=None,
            start_ns=time.perf_counter_ns(),
            status="validation_error",
            error_message=error_message,
        )
