"""AgentRuntime — core orchestrator for Agent execution.

Responsibilities:
- Load Agent identity (SOUL.md, IDENTITY.md) via IdentityLoader
- Inject Skills knowledge into the system prompt
- Build the governance-filtered visible tools list
- Execute the LLM function-calling decision loop (via litellm)
- Provide trigger_event() as the public entry point for cron/webhook/etc.

This MVP implementation omits:
- Long-term memory (vector search) — returns empty; add later with MemorySystem
- Langfuse tracing — optional; add later with integrations-langfuse
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import math
import os
import re
import time
from collections import OrderedDict, deque
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from owlclaw.agent.runtime.config import (
    DEFAULT_RUNTIME_CONFIG,
    load_runtime_config,
    merge_runtime_config,
)
from owlclaw.agent.runtime.context import AgentRunContext
from owlclaw.agent.runtime.heartbeat import HeartbeatChecker
from owlclaw.agent.runtime.identity import IdentityLoader
from owlclaw.integrations import llm as llm_integration
from owlclaw.integrations.langfuse import TraceContext
from owlclaw.security.audit import SecurityAuditLog
from owlclaw.security.sanitizer import InputSanitizer

if TYPE_CHECKING:
    from owlclaw.agent.tools import BuiltInTools
    from owlclaw.capabilities.knowledge import KnowledgeInjector
    from owlclaw.capabilities.registry import CapabilityRegistry
    from owlclaw.governance.ledger import Ledger
    from owlclaw.governance.router import Router
    from owlclaw.governance.visibility import VisibilityFilter

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_MAX_ITERATIONS = 50
_DEFAULT_LLM_TIMEOUT_SECONDS = 60.0
_DEFAULT_RUN_TIMEOUT_SECONDS = 300.0
_DEFAULT_LLM_RETRY_ATTEMPTS = 1
_CHARS_PER_TOKEN = 4
_SKILL_ENV_PREFIX = "OWLCLAW_SKILL_"
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
}
_INTERNAL_ERROR_MESSAGE = "Tool execution failed due to an internal error."


def _coerce_confirmation_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return False


def _normalize_risk_level(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"low", "medium", "high", "critical"}:
            return normalized
    return "low"


class AgentRuntime:
    """Core orchestrator for a single Agent.

    All constructor dependencies are optional so the runtime can be
    instantiated even before the full OwlClaw app is assembled; call
    :meth:`setup` before :meth:`run` or :meth:`trigger_event`.

    Args:
        agent_id: Stable name for this Agent (usually the OwlClaw app name).
        app_dir: Path to the application directory containing SOUL.md /
            IDENTITY.md and the capabilities folder.
        registry: Registered capability handlers and state providers.
        knowledge_injector: Formats Skills content for the system prompt.
        visibility_filter: Governance-layer capability filter; if *None* all
            registered capabilities are visible.
        router: Optional model router (task_type → model); if set, used before
            each LLM call instead of fixed *model*.
        ledger: Optional execution ledger; if set, capability runs are recorded.
        model: LLM model string (default when router is None or returns None).
        config: Optional runtime configuration overrides.
    """

    def __init__(
        self,
        agent_id: str,
        app_dir: str,
        *,
        registry: CapabilityRegistry | None = None,
        knowledge_injector: KnowledgeInjector | None = None,
        visibility_filter: VisibilityFilter | None = None,
        builtin_tools: BuiltInTools | None = None,
        router: Router | None = None,
        ledger: Ledger | None = None,
        signal_state_manager: Any | None = None,
        model: str = _DEFAULT_MODEL,
        config: dict[str, Any] | None = None,
        config_path: str | None = None,
    ) -> None:
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id must be a non-empty string")
        if not isinstance(app_dir, str) or not app_dir.strip():
            raise ValueError("app_dir must be a non-empty string")
        self.agent_id = agent_id.strip()
        self.app_dir = app_dir.strip()
        self.registry = registry
        self.knowledge_injector = knowledge_injector
        self.visibility_filter = visibility_filter
        self.builtin_tools = builtin_tools
        self._router = router
        self._ledger = ledger
        self._signal_state_manager = signal_state_manager
        self.model = model
        base_config = dict(DEFAULT_RUNTIME_CONFIG)
        user_config = dict(config or {})
        self.config = merge_runtime_config(base_config, user_config)
        if config_path:
            file_config = load_runtime_config(config_path)
            self.config = merge_runtime_config(self.config, file_config)
            loaded_model = file_config.get("model")
            if isinstance(loaded_model, str) and loaded_model.strip():
                self.model = loaded_model.strip()
        self._config_path = config_path
        user_model = user_config.get("model")
        if isinstance(user_model, str) and user_model.strip():
            self.model = user_model.strip()
        self._input_sanitizer = InputSanitizer()
        self._security_audit = SecurityAuditLog()

        self._identity_loader: IdentityLoader | None = None
        self._heartbeat_checker: HeartbeatChecker | None = None
        self._langfuse_init_error: str | None = None
        self._langfuse = self._init_langfuse_client()
        self._tool_call_timestamps: deque[float] = deque()
        self._tool_call_timestamps_lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        self._skills_context_cache: OrderedDict[tuple[str, str | None, tuple[str, ...]], str] = OrderedDict()
        self._visible_tools_cache: OrderedDict[tuple[str, str, tuple[str, ...]], list[dict[str, Any]]] = OrderedDict()
        self._run_handler_names_snapshot: tuple[str, ...] | None = None
        self._run_capabilities_snapshot: list[dict[str, Any]] | None = None
        self._run_env_restore: dict[str, str | None] = {}
        self._perf_metrics: dict[str, float] = {
            "llm_calls": 0.0,
            "tool_calls": 0.0,
            "skills_cache_hits": 0.0,
            "tools_cache_hits": 0.0,
            "llm_time_ms_total": 0.0,
        }
        self._migration_gate = self._init_migration_gate()
        self._approval_queue = self._init_approval_queue()
        self.is_initialized = False

    def _resolve_migration_config_path(self) -> Path:
        return Path(self.app_dir) / "owlclaw.yaml"

    def _init_migration_gate(self) -> Any:
        try:
            from owlclaw.governance import MigrationGate

            return MigrationGate(config_path=self._resolve_migration_config_path())
        except Exception:
            logger.debug("MigrationGate initialization failed", exc_info=True)
            return None

    def _init_approval_queue(self) -> Any:
        try:
            from owlclaw.config.loader import YAMLConfigLoader
            from owlclaw.governance import InMemoryApprovalQueue

            config = YAMLConfigLoader.load_dict(self._resolve_migration_config_path())
            timeout_seconds = 24 * 60 * 60
            migration_cfg = config.get("migration")
            if isinstance(migration_cfg, dict):
                approval_cfg = migration_cfg.get("approval")
                if isinstance(approval_cfg, dict):
                    timeout_raw = approval_cfg.get("timeout_seconds")
                    if isinstance(timeout_raw, int) and timeout_raw > 0:
                        timeout_seconds = timeout_raw
            return InMemoryApprovalQueue(timeout_seconds=timeout_seconds)
        except Exception:
            logger.debug("Approval queue initialization failed", exc_info=True)
            return None

    def _init_langfuse_client(self) -> Any | None:
        """Initialize optional Langfuse client from runtime config."""
        cfg = self.config.get("langfuse")
        if not isinstance(cfg, dict):
            return None
        injected = cfg.get("client")
        if injected is not None:
            return injected
        if not cfg.get("enabled", False):
            return None

        public_key = cfg.get("public_key") or os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = cfg.get("secret_key") or os.environ.get("LANGFUSE_SECRET_KEY")
        host = cfg.get("host", "https://cloud.langfuse.com")
        if not public_key or not secret_key:
            return None
        try:
            module = importlib.import_module("langfuse")
            langfuse_cls = module.Langfuse
            try:
                return langfuse_cls(
                    public_key=public_key,
                    secret_key=secret_key,
                    base_url=host,
                )
            except TypeError:
                return langfuse_cls(
                    public_key=public_key,
                    secret_key=secret_key,
                    host=host,
                )
        except Exception as exc:
            self._langfuse_init_error = str(exc)
            logger.warning("Langfuse init failed: %s", exc)
            return None

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        if isinstance(exc, FileNotFoundError):
            return "initialization_error"
        if isinstance(exc, asyncio.TimeoutError):
            return "timeout_error"
        if isinstance(exc, ValueError):
            return "validation_error"
        if isinstance(exc, ConnectionError | OSError):
            return "dependency_error"
        return "runtime_error"

    @staticmethod
    def _safe_error_message(exc: Exception) -> str:
        """Return a sanitized exception description safe for logs/records."""
        return exc.__class__.__name__

    async def _notify_error(
        self,
        *,
        context: AgentRunContext | None,
        stage: str,
        category: str,
        message: str,
    ) -> None:
        notifier = self.config.get("error_notifier")
        if not callable(notifier):
            return
        payload = {
            "agent_id": self.agent_id,
            "run_id": context.run_id if context else None,
            "trigger": context.trigger if context else None,
            "stage": stage,
            "category": category,
            "message": message,
        }
        try:
            maybe_awaitable = notifier(payload)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception as exc:
            logger.warning("error_notifier failed: %s", exc)

    def _create_trace(self, context: AgentRunContext) -> Any | None:
        if self._langfuse is None:
            return None
        trace_fn = getattr(self._langfuse, "trace", None)
        if not callable(trace_fn):
            return None
        try:
            return trace_fn(
                name="agent_run",
                metadata={
                    "agent_id": context.agent_id,
                    "run_id": context.run_id,
                    "trigger": context.trigger,
                    "focus": context.focus,
                    "tenant_id": context.tenant_id,
                },
            )
        except Exception as exc:
            logger.warning("Langfuse trace create failed: %s", exc)
            return None

    @staticmethod
    def _update_trace(trace: Any | None, **kwargs: Any) -> None:
        if trace is None:
            return
        update_fn = getattr(trace, "update", None)
        if callable(update_fn):
            try:
                update_fn(**kwargs)
            except Exception:
                logger.debug("Langfuse trace update failed", exc_info=True)

    @staticmethod
    def _observe_tool(trace: Any | None, name: str, payload: dict[str, Any]) -> Any | None:
        if trace is None:
            return None
        span_fn = getattr(trace, "span", None)
        if callable(span_fn):
            try:
                return span_fn(name=name, input=payload)
            except Exception:
                logger.debug("Langfuse span create failed", exc_info=True)
        event_fn = getattr(trace, "event", None)
        if callable(event_fn):
            try:
                return event_fn(name=name, input=payload)
            except Exception:
                logger.debug("Langfuse event create failed", exc_info=True)
        return None

    @staticmethod
    def _finish_observation(observation: Any | None, **kwargs: Any) -> None:
        if observation is None:
            return
        for method_name in ("end", "update"):
            method = getattr(observation, method_name, None)
            if not callable(method):
                continue
            try:
                method(**kwargs)
                return
            except TypeError:
                try:
                    method()
                    return
                except Exception:
                    logger.debug("Langfuse observation %s() failed", method_name, exc_info=True)
                    continue
            except Exception:
                logger.debug("Langfuse observation %s() failed", method_name, exc_info=True)
                continue

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Initialize the runtime.

        Loads Agent identity from *app_dir* and marks the runtime ready.

        Raises:
            FileNotFoundError: If SOUL.md is missing from *app_dir*.
        """
        try:
            self._identity_loader = IdentityLoader(self.app_dir)
            await self._identity_loader.load()
            hb_config = self.config.get("heartbeat", {})
            if hb_config.get("enabled", True):
                self._heartbeat_checker = HeartbeatChecker(
                    self.agent_id,
                    hb_config,
                    ledger=self._ledger,
                )
            self.is_initialized = True
            logger.info("AgentRuntime '%s' initialized", self.agent_id)
        except Exception as exc:
            category = self._classify_error(exc)
            await self._notify_error(
                context=None,
                stage="setup",
                category=category,
                message=self._safe_error_message(exc),
            )
            raise

    def load_config_file(self, path: str) -> dict[str, Any]:
        """Load runtime config from YAML and apply it."""
        self._validate_path_within_app_dir(path)
        loaded = load_runtime_config(path)
        self.config = merge_runtime_config(self.config, loaded)
        self._config_path = path
        self.model = str(self.config.get("model", self.model))
        return dict(self.config)

    def reload_config(self) -> dict[str, Any]:
        """Reload runtime config from the last loaded config file."""
        if not self._config_path:
            raise RuntimeError("config path is not set")
        return self.load_config_file(self._config_path)

    def get_performance_metrics(self) -> dict[str, float]:
        """Get in-memory runtime performance counters."""
        return dict(self._perf_metrics)

    def _validate_path_within_app_dir(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser().resolve()
        app_root = Path(self.app_dir).resolve()
        if candidate == app_root:
            return candidate
        if app_root not in candidate.parents:
            raise ValueError("path must stay within app_dir")
        return candidate

    # ------------------------------------------------------------------
    # Public trigger entry point
    # ------------------------------------------------------------------

    async def trigger_event(
        self,
        event_name: str,
        *,
        focus: str | None = None,
        payload: dict[str, Any] | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Trigger an Agent run from an external event (cron, webhook, etc.).

        This is the primary API consumed by :class:`CronTriggerRegistry` and
        future trigger adapters.  It creates an :class:`AgentRunContext` and
        calls :meth:`run`.

        Args:
            event_name: Human-readable event name used as the run trigger.
            focus: Optional focus tag to narrow Skill selection.
            payload: Arbitrary context forwarded to the decision loop.
            tenant_id: Multi-tenancy identifier.

        Returns:
            Run result dict (see :meth:`run`).
        """
        if not isinstance(event_name, str) or not event_name.strip():
            raise ValueError("event_name must be a non-empty string")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        normalized_event_name = event_name.strip()
        normalized_tenant_id = tenant_id.strip()
        if isinstance(focus, str):
            focus = focus.strip() or None
        elif focus is not None:
            focus = None
        if payload is None:
            normalized_payload: dict[str, Any] = {}
        elif not isinstance(payload, dict):
            raise ValueError("payload must be a dictionary")
        else:
            normalized_payload = dict(payload)
        context = AgentRunContext(
            agent_id=self.agent_id,
            trigger=normalized_event_name,
            payload=normalized_payload,
            focus=focus,
            tenant_id=normalized_tenant_id,
        )
        await self._inject_pending_signal_instructions(context)
        if normalized_event_name != "signal_manual":
            paused = await self._is_agent_paused(normalized_tenant_id)
            if paused:
                await self._record_paused_skip(context)
                return {
                    "status": "skipped",
                    "run_id": context.run_id,
                    "reason": "agent_paused",
                }
        return await self.run(context)

    async def _is_agent_paused(self, tenant_id: str) -> bool:
        if self._signal_state_manager is None:
            return False
        try:
            state = await self._signal_state_manager.get(self.agent_id, tenant_id)
        except Exception:
            logger.debug("Signal state get failed", exc_info=True)
            return False
        return bool(getattr(state, "paused", False))

    async def _inject_pending_signal_instructions(self, context: AgentRunContext) -> None:
        if self._signal_state_manager is None:
            return
        try:
            consumed = await self._signal_state_manager.consume_instructions(
                self.agent_id,
                context.tenant_id,
            )
        except Exception:
            logger.debug("Signal instruction consume failed", exc_info=True)
            return
        if not consumed:
            return
        context.payload["signal_instructions"] = [
            {
                "content": str(getattr(item, "content", "")),
                "operator": str(getattr(item, "operator", "")),
                "source": str(getattr(getattr(item, "source", ""), "value", getattr(item, "source", ""))),
                "created_at": str(getattr(item, "created_at", "")),
                "expires_at": str(getattr(item, "expires_at", "")),
            }
            for item in consumed
        ]
        await self._record_instruction_consumption(context, count=len(consumed))

    async def _record_paused_skip(self, context: AgentRunContext) -> None:
        if self._ledger is None:
            return
        try:
            await self._ledger.record_execution(
                tenant_id=context.tenant_id,
                agent_id=context.agent_id,
                run_id=context.run_id,
                capability_name="signal.pause_guard",
                task_type="signal",
                input_params={"trigger": context.trigger},
                output_result={"status": "skipped", "reason": "agent_paused"},
                decision_reasoning="paused guard in runtime",
                execution_time_ms=0,
                llm_model="",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status="skipped",
                error_message=None,
            )
        except Exception:
            logger.debug("Ledger paused skip record failed", exc_info=True)

    async def _record_instruction_consumption(self, context: AgentRunContext, *, count: int) -> None:
        if self._ledger is None:
            return
        try:
            await self._ledger.record_execution(
                tenant_id=context.tenant_id,
                agent_id=context.agent_id,
                run_id=context.run_id,
                capability_name="signal.consume_instructions",
                task_type="signal",
                input_params={"trigger": context.trigger},
                output_result={"consumed_count": count},
                decision_reasoning="runtime instruction injection",
                execution_time_ms=0,
                llm_model="",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status="success",
                error_message=None,
            )
        except Exception:
            logger.debug("Ledger instruction consumption record failed", exc_info=True)

    # ------------------------------------------------------------------
    # Core run method
    # ------------------------------------------------------------------

    async def run(self, context: AgentRunContext) -> dict[str, Any]:
        """Execute a full Agent run for *context*.

        Args:
            context: Run context including trigger source, focus, and payload.

        Returns:
            ``{"status": "completed"|"skipped", "run_id": str, ...}``

        Raises:
            RuntimeError: If :meth:`setup` has not been called yet.
        """
        if not self.is_initialized:
            raise RuntimeError(
                "AgentRuntime.setup() must be called before run()"
            )

        logger.info(
            "Agent run started agent_id=%s run_id=%s trigger=%s focus=%s",
            context.agent_id,
            context.run_id,
            context.trigger,
            context.focus,
        )
        async with self._run_lock:
            self._capture_run_skill_snapshot()
            self._inject_skill_env_for_run()

            if context.trigger == "heartbeat":
                if self._heartbeat_checker is None:
                    logger.info(
                        "Heartbeat checker unavailable, running decision loop directly agent_id=%s run_id=%s",
                        context.agent_id,
                        context.run_id,
                    )
                else:
                    has_events = self._heartbeat_payload_has_events(context.payload)
                    if not has_events:
                        has_events = await self._heartbeat_checker.check_events(context.tenant_id)
                    if not has_events:
                        logger.info(
                            "Heartbeat no events, skipping LLM agent_id=%s run_id=%s",
                            context.agent_id,
                            context.run_id,
                        )
                        self._reset_builtin_tool_budget(context.run_id)
                        self._release_skill_content_cache()
                        self._clear_run_skill_snapshot()
                        self._restore_skill_env_after_run()
                        return {
                            "status": "skipped",
                            "run_id": context.run_id,
                            "reason": "heartbeat_no_events",
                        }

            trace = self._create_trace(context)
            previous_trace_ctx = TraceContext.get_current()
            trace_id = getattr(trace, "id", None)
            if isinstance(trace_id, str) and trace_id:
                TraceContext.set_current(
                    TraceContext(
                        trace_id=trace_id,
                        metadata={
                            "agent_id": context.agent_id,
                            "run_id": context.run_id,
                            "langfuse_trace": trace,
                        },
                    )
                )

            run_timeout_raw = self.config.get(
                "run_timeout_seconds", _DEFAULT_RUN_TIMEOUT_SECONDS
            )
            if isinstance(run_timeout_raw, bool):
                run_timeout = _DEFAULT_RUN_TIMEOUT_SECONDS
            else:
                try:
                    run_timeout = float(run_timeout_raw)
                except (TypeError, ValueError):
                    run_timeout = _DEFAULT_RUN_TIMEOUT_SECONDS
            if run_timeout <= 0:
                run_timeout = _DEFAULT_RUN_TIMEOUT_SECONDS
            if not math.isfinite(run_timeout):
                run_timeout = _DEFAULT_RUN_TIMEOUT_SECONDS
            try:
                decision_loop_fn = self._decision_loop
                decision_loop_params = inspect.signature(decision_loop_fn).parameters
                if len(decision_loop_params) >= 2:
                    decision_loop_coro = decision_loop_fn(context, trace)
                else:
                    decision_loop_coro = decision_loop_fn(context)
                result = await asyncio.wait_for(
                    decision_loop_coro,
                    timeout=run_timeout,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Agent run timed out agent_id=%s run_id=%s timeout=%ss",
                    context.agent_id,
                    context.run_id,
                    run_timeout,
                )
                self._update_trace(
                    trace,
                    status="error",
                    output=f"run timed out after {run_timeout:.1f}s",
                )
                await self._notify_error(
                    context=context,
                    stage="run",
                    category="timeout_error",
                    message=f"run timed out after {run_timeout:.1f}s",
                )
                return {
                    "status": "failed",
                    "run_id": context.run_id,
                    "error": f"run timed out after {run_timeout:.1f}s",
                }
            finally:
                self._reset_builtin_tool_budget(context.run_id)
                self._release_skill_content_cache()
                self._clear_run_skill_snapshot()
                self._restore_skill_env_after_run()
                TraceContext.set_current(previous_trace_ctx)

            logger.info(
                "Agent run completed agent_id=%s run_id=%s iterations=%s",
                context.agent_id,
                context.run_id,
                result.get("iterations", 0),
            )
            self._update_trace(trace, status="success", output=result)
            return {"status": "completed", "run_id": context.run_id, **result}

    def _reset_builtin_tool_budget(self, run_id: str) -> None:
        """Reset built-in tool run budget after each run to avoid state growth."""
        if self.builtin_tools is None:
            return
        reset_budget = getattr(self.builtin_tools, "reset_run_call_budget", None)
        if not callable(reset_budget):
            return
        try:
            reset_budget(run_id)
        except Exception:
            logger.exception("Failed to reset built-in tool budget run_id=%s", run_id)

    # ------------------------------------------------------------------
    # Decision loop
    # ------------------------------------------------------------------

    async def _decision_loop(self, context: AgentRunContext, trace: Any | None = None) -> dict[str, Any]:
        """Core LLM function-calling loop.

        1. Build system prompt (identity + skills knowledge)
        2. Build visible tools list
        3. Iterate: call LLM → execute tool calls → repeat until no tool calls
        """
        # Build context components
        skills_context = self._build_skills_context(context)
        visible_tools = await self._get_visible_tools(context)
        messages = self._build_messages(context, skills_context, visible_tools)

        max_iterations_raw = self.config.get(
            "max_function_calls", _DEFAULT_MAX_ITERATIONS
        )
        if isinstance(max_iterations_raw, bool):
            max_iterations = _DEFAULT_MAX_ITERATIONS
        else:
            try:
                max_iterations = max(1, int(max_iterations_raw))
            except (TypeError, ValueError):
                max_iterations = _DEFAULT_MAX_ITERATIONS
        llm_timeout_raw = self.config.get(
            "llm_timeout_seconds", _DEFAULT_LLM_TIMEOUT_SECONDS
        )
        if isinstance(llm_timeout_raw, bool):
            llm_timeout = _DEFAULT_LLM_TIMEOUT_SECONDS
        else:
            try:
                llm_timeout = float(llm_timeout_raw)
            except (TypeError, ValueError):
                llm_timeout = _DEFAULT_LLM_TIMEOUT_SECONDS
        if llm_timeout <= 0:
            llm_timeout = _DEFAULT_LLM_TIMEOUT_SECONDS
        if not math.isfinite(llm_timeout):
            llm_timeout = _DEFAULT_LLM_TIMEOUT_SECONDS
        model_used = self.model
        iteration = 0
        exhausted_without_completion = False
        for _ in range(max_iterations):
            iteration += 1
            if self._router is not None:
                from owlclaw.governance.visibility import RunContext

                task_type = context.payload.get("task_type") or self.config.get("default_task_type") or "default"
                run_ctx = RunContext(tenant_id=context.tenant_id)
                selection = await self._router.select_model(task_type, run_ctx)
                if selection is not None and getattr(selection, "model", None):
                    model_used = selection.model

            call_kwargs: dict[str, Any] = {
                "model": model_used,
                "messages": messages,
            }
            if visible_tools:
                call_kwargs["tools"] = visible_tools
                call_kwargs["tool_choice"] = "auto"

            try:
                llm_started_ns = time.perf_counter_ns()
                response, model_used, llm_tokens_input, llm_tokens_output, llm_cost = await self._call_llm_completion(
                    call_kwargs,
                    timeout=llm_timeout,
                )
                llm_elapsed_ms = (time.perf_counter_ns() - llm_started_ns) // 1_000_000
                self._perf_metrics["llm_calls"] += 1
                self._perf_metrics["llm_time_ms_total"] += float(llm_elapsed_ms)
                await self._record_llm_usage(
                    context=context,
                    model=model_used,
                    llm_tokens_input=llm_tokens_input,
                    llm_tokens_output=llm_tokens_output,
                    estimated_cost=llm_cost,
                    execution_time_ms=llm_elapsed_ms,
                    status="success",
                    error_message=None,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "LLM call timed out agent_id=%s run_id=%s timeout=%ss",
                    context.agent_id,
                    context.run_id,
                    llm_timeout,
                )
                await self._record_llm_usage(
                    context=context,
                    model=model_used,
                    llm_tokens_input=0,
                    llm_tokens_output=0,
                    estimated_cost=Decimal("0"),
                    execution_time_ms=int(llm_timeout * 1000),
                    status="timeout",
                    error_message=f"LLM call timed out after {llm_timeout:.1f}s",
                )
                messages.append(
                    {
                        "role": "assistant",
                        "content": f"LLM call timed out after {llm_timeout:.1f}s.",
                    }
                )
                break
            except Exception as exc:
                await self._record_llm_usage(
                    context=context,
                    model=model_used,
                    llm_tokens_input=0,
                    llm_tokens_output=0,
                    estimated_cost=Decimal("0"),
                    execution_time_ms=0,
                    status="error",
                    error_message=self._safe_error_message(exc),
                )
                logger.error("LLM call failed agent_id=%s run_id=%s error=%s", context.agent_id, context.run_id, exc)
                await self._notify_error(
                    context=context,
                    stage="llm_call",
                    category=self._classify_error(exc),
                    message=self._safe_error_message(exc),
                )
                messages.append(
                    {
                        "role": "assistant",
                        "content": "LLM call failed due to an internal error.",
                    }
                )
                break
            message = self._extract_assistant_message(response)
            if message is None:
                logger.error(
                    "Invalid LLM response shape agent_id=%s run_id=%s",
                    context.agent_id,
                    context.run_id,
                )
                messages.append({
                    "role": "assistant",
                    "content": "LLM response missing assistant message.",
                })
                break

            # Append assistant turn to conversation
            messages.append(self._assistant_message_to_dict(message))

            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                # LLM decided it is done
                break

            # Execute each tool call and add results
            for idx, tc in enumerate(tool_calls):
                tool_result = await self._execute_tool(tc, context, trace=trace)
                tool_call_id = getattr(tc, "id", None) or f"tool_call_{iteration}_{idx}"
                tool_name = getattr(getattr(tc, "function", None), "name", None) or "unknown_tool"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": self._sanitize_tool_result(
                        tool_result,
                        tool_name=str(tool_name),
                        run_id=context.run_id,
                    ),
                })
        else:
            exhausted_without_completion = True

        if exhausted_without_completion and messages and messages[-1].get("role") == "tool":
            try:
                llm_started_ns = time.perf_counter_ns()
                final_response, model_used, llm_tokens_input, llm_tokens_output, llm_cost = await self._call_llm_completion(
                    {"model": model_used, "messages": messages},
                    timeout=llm_timeout,
                )
                llm_elapsed_ms = (time.perf_counter_ns() - llm_started_ns) // 1_000_000
                self._perf_metrics["llm_calls"] += 1
                self._perf_metrics["llm_time_ms_total"] += float(llm_elapsed_ms)
                await self._record_llm_usage(
                    context=context,
                    model=model_used,
                    llm_tokens_input=llm_tokens_input,
                    llm_tokens_output=llm_tokens_output,
                    estimated_cost=llm_cost,
                    execution_time_ms=llm_elapsed_ms,
                    status="success",
                    error_message=None,
                )
                final_message = self._extract_assistant_message(final_response)
                if final_message is not None:
                    messages.append(self._assistant_message_to_dict(final_message))
                else:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": "Reached max iterations; final summarization response was invalid.",
                        }
                    )
            except asyncio.TimeoutError:
                messages.append(
                    {
                        "role": "assistant",
                        "content": f"Reached max iterations ({max_iterations}) and final summarization timed out.",
                    }
                )
            except Exception as exc:
                logger.warning(
                    "Final summarization failed after max iterations (max_iterations=%s): %s",
                    max_iterations,
                    exc,
                    exc_info=True,
                )
                messages.append(
                    {
                        "role": "assistant",
                        "content": "Reached max iterations and final summarization failed due to an internal error.",
                    }
                )

        final_content = ""
        if messages and messages[-1].get("role") == "assistant":
            final_content = messages[-1].get("content") or ""

        return {
            "iterations": iteration,
            "final_response": final_content,
            "tool_calls_total": sum(
                1
                for m in messages
                if m.get("role") == "tool"
            ),
        }

    def _sanitize_tool_result(self, tool_result: Any, *, tool_name: str, run_id: str) -> str:
        """Sanitize serialized tool result before appending to tool role message."""
        serialized = json.dumps(tool_result, default=str)
        sanitized = self._input_sanitizer.sanitize(
            serialized,
            source=f"tool_result_message:{tool_name}",
        )
        if sanitized.changed:
            self._security_audit.record(
                event_type="tool_result_sanitized",
                source="runtime",
                details={
                    "tool_name": tool_name,
                    "run_id": run_id,
                    "modifications": sanitized.modifications,
                    "stage": "decision_loop_message",
                },
            )
        return sanitized.sanitized

    @staticmethod
    def _extract_usage_tokens(response: Any) -> tuple[int, int]:
        usage = (
            response.get("usage")
            if isinstance(response, dict)
            else getattr(response, "usage", None)
        )
        if usage is None:
            return 0, 0
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
        else:
            prompt_tokens = getattr(usage, "prompt_tokens", 0)
            completion_tokens = getattr(usage, "completion_tokens", 0)
        try:
            prompt = int(prompt_tokens)
        except (TypeError, ValueError):
            prompt = 0
        try:
            completion = int(completion_tokens)
        except (TypeError, ValueError):
            completion = 0
        return max(0, prompt), max(0, completion)

    async def _call_llm_completion(
        self,
        call_kwargs: dict[str, Any],
        *,
        timeout: float,
    ) -> tuple[Any, str, int, int, Decimal]:
        """Call LLM facade with retry and fallback model support."""
        retries_raw = self.config.get("llm_retry_attempts", _DEFAULT_LLM_RETRY_ATTEMPTS)
        if isinstance(retries_raw, bool):
            retries = _DEFAULT_LLM_RETRY_ATTEMPTS
        else:
            try:
                retries = max(1, int(retries_raw))
            except (TypeError, ValueError):
                retries = _DEFAULT_LLM_RETRY_ATTEMPTS

        fallback_raw = self.config.get("llm_fallback_models", [])
        fallback_models: list[str] = []
        if isinstance(fallback_raw, list):
            for item in fallback_raw:
                if isinstance(item, str) and item.strip():
                    fallback_models.append(item.strip())

        primary_model = str(call_kwargs.get("model", self.model))
        model_chain = [primary_model] + [m for m in fallback_models if m != primary_model]
        last_error: Exception | None = None

        for model_name in model_chain:
            kwargs = dict(call_kwargs)
            kwargs["model"] = model_name
            for _ in range(retries):
                try:
                    response = await asyncio.wait_for(
                        llm_integration.acompletion(**kwargs),
                        timeout=timeout,
                    )
                    cost_info = llm_integration.extract_cost_info(response, model=model_name)
                    return (
                        response,
                        model_name,
                        cost_info.prompt_tokens,
                        cost_info.completion_tokens,
                        Decimal(str(cost_info.total_cost)),
                    )
                except asyncio.TimeoutError:
                    raise
                except Exception as exc:
                    last_error = exc
                    continue

        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM call failed without an explicit error")

    async def _record_llm_usage(
        self,
        *,
        context: AgentRunContext,
        model: str,
        llm_tokens_input: int,
        llm_tokens_output: int,
        estimated_cost: Decimal,
        execution_time_ms: int,
        status: str,
        error_message: str | None,
    ) -> None:
        if self._ledger is None:
            return
        task_type = context.payload.get("task_type") or self.config.get("default_task_type") or "default"
        try:
            await self._ledger.record_execution(
                tenant_id=context.tenant_id,
                agent_id=context.agent_id,
                run_id=context.run_id,
                capability_name="llm_completion",
                task_type=str(task_type),
                input_params={"trigger": context.trigger, "focus": context.focus},
                output_result={"status": status},
                decision_reasoning="runtime_llm_call",
                execution_time_ms=max(0, int(execution_time_ms)),
                llm_model=model or "",
                llm_tokens_input=max(0, int(llm_tokens_input)),
                llm_tokens_output=max(0, int(llm_tokens_output)),
                estimated_cost=max(Decimal("0"), estimated_cost),
                status=status,
                error_message=error_message,
            )
        except Exception as exc:
            logger.warning("Failed to record LLM usage in ledger: %s", exc)

    @staticmethod
    def _assistant_message_to_dict(message: Any) -> dict[str, Any]:
        """Normalize LLM message object to a serializable assistant dict."""
        if isinstance(message, dict):
            normalized = dict(message)
            normalized.setdefault("role", "assistant")
            return normalized
        model_dump = getattr(message, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(exclude_none=True)
            if isinstance(dumped, dict):
                dumped.setdefault("role", "assistant")
                return dumped
        content = getattr(message, "content", "")
        tool_calls = getattr(message, "tool_calls", None)
        out: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            out["tool_calls"] = tool_calls
        return out

    @staticmethod
    def _extract_assistant_message(response: Any) -> Any | None:
        """Extract assistant message from completion response."""
        choices = (
            response.get("choices")
            if isinstance(response, dict)
            else getattr(response, "choices", None)
        )
        if not isinstance(choices, list | tuple) or not choices:
            return None
        first = choices[0]
        if isinstance(first, dict):
            return first.get("message")
        return getattr(first, "message", None)

    @staticmethod
    def _heartbeat_payload_has_events(payload: dict[str, Any]) -> bool:
        """Return True when heartbeat payload already indicates pending events.

        This path keeps Heartbeat checks zero-I/O by trusting trigger-side
        in-memory signals carried in payload.
        """
        if not payload:
            return False
        if payload.get("has_events") is True:
            return True
        pending = payload.get("pending_events")
        if isinstance(pending, list | tuple | set) and len(pending) > 0:
            return True
        count = payload.get("event_count")
        return bool(
            isinstance(count, int | float)
            and not isinstance(count, bool)
            and count > 0
        )

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(
        self, tool_call: Any, context: AgentRunContext, *, trace: Any | None = None
    ) -> Any:
        """Dispatch a single LLM tool call to the capability registry.

        Falls back to a descriptive error dict if the capability is not
        registered or raises, so the LLM can handle it gracefully.
        """
        function = getattr(tool_call, "function", None)
        tool_name = getattr(function, "name", None)
        if not isinstance(tool_name, str) or not tool_name.strip():
            return {"error": "Invalid tool call: missing function name"}
        tool_name = tool_name.strip()
        invalid_arguments = False
        invalid_reason = ""
        try:
            raw_args = getattr(function, "arguments", None)
            if raw_args is None:
                raise AttributeError
            arguments: dict[str, Any] = (
                json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            )
        except json.JSONDecodeError:
            invalid_arguments = True
            invalid_reason = "arguments must be valid JSON object"
            arguments = {}
        except AttributeError:
            invalid_arguments = True
            invalid_reason = "missing tool arguments"
            arguments = {}
        if not isinstance(arguments, dict):
            invalid_arguments = True
            invalid_reason = "arguments must be a JSON object"
            arguments = {}
        if invalid_arguments:
            return {
                "error": f"Invalid arguments for tool '{tool_name}': {invalid_reason}",
            }

        permission_error = self._enforce_tool_permissions(tool_name)
        if permission_error is not None:
            self._security_audit.record(
                event_type="tool_permission_denied",
                source="runtime",
                details={"tool_name": tool_name, "run_id": context.run_id, "reason": permission_error},
            )
            return {"error": permission_error}

        rate_limit_error = await self._enforce_rate_limit(tool_name)
        if rate_limit_error is not None:
            self._security_audit.record(
                event_type="tool_rate_limited",
                source="runtime",
                details={"tool_name": tool_name, "run_id": context.run_id, "reason": rate_limit_error},
            )
            return {"error": rate_limit_error}

        if self.builtin_tools is not None and self.builtin_tools.is_builtin(tool_name):
            from owlclaw.agent.tools import BuiltInToolsContext

            builtin_arguments, arg_modifications = self._sanitize_tool_payload(
                arguments,
                source=f"tool_args:{tool_name}",
            )
            if arg_modifications:
                self._security_audit.record(
                    event_type="tool_arguments_sanitized",
                    source="runtime",
                    details={
                        "tool_name": tool_name,
                        "run_id": context.run_id,
                        "modifications": arg_modifications,
                    },
                )
            ctx = BuiltInToolsContext(
                agent_id=context.agent_id,
                run_id=context.run_id,
                tenant_id=context.tenant_id,
            )
            self._perf_metrics["tool_calls"] += 1
            observation = self._observe_tool(
                trace,
                "tool_execution",
                {"tool": tool_name, "run_id": context.run_id, "arguments": builtin_arguments},
            )
            try:
                result = await self.builtin_tools.execute(tool_name, builtin_arguments, ctx)
                result, result_modifications = self._sanitize_tool_payload(
                    result,
                    source=f"tool_result:{tool_name}",
                )
                if result_modifications:
                    self._security_audit.record(
                        event_type="tool_result_sanitized",
                        source="runtime",
                        details={
                            "tool_name": tool_name,
                            "run_id": context.run_id,
                            "modifications": result_modifications,
                        },
                    )
                self._finish_observation(
                    observation,
                    output=result if isinstance(result, dict) else {"result": result},
                    status="success",
                )
                return result
            except Exception:
                logger.exception("Built-in tool '%s' failed", tool_name)
                self._finish_observation(
                    observation,
                    status="error",
                    output={"error": _INTERNAL_ERROR_MESSAGE},
                )
                return {"error": _INTERNAL_ERROR_MESSAGE}

        if self.registry is None:
            return {"error": f"No capability registry configured for tool '{tool_name}'"}

        invoke_arguments = self._normalize_capability_arguments(arguments, context)
        invoke_arguments, arg_modifications = self._sanitize_tool_payload(
            invoke_arguments,
            source=f"tool_args:{tool_name}",
        )
        if arg_modifications:
            self._security_audit.record(
                event_type="tool_arguments_sanitized",
                source="runtime",
                details={
                    "tool_name": tool_name,
                    "run_id": context.run_id,
                    "modifications": arg_modifications,
                },
            )
        validation_error = self._validate_tool_arguments(tool_name, invoke_arguments)
        if validation_error is not None:
            self._security_audit.record(
                event_type="tool_arguments_validation_failed",
                source="runtime",
                details={
                    "tool_name": tool_name,
                    "run_id": context.run_id,
                    "reason": validation_error,
                },
            )
            return {"error": validation_error}
        capability_meta = self.registry.get_capability_metadata(tool_name)
        skill_owlclaw = self._get_skill_owlclaw_config(tool_name)
        migration_outcome = self._evaluate_migration(tool_name, invoke_arguments, skill_owlclaw)
        if migration_outcome["decision"] == "observe_only":
            observed = {
                "status": "observe_only",
                "tool": tool_name,
                "reason": "migration_weight is 0 or policy returned observe_only",
                "arguments": invoke_arguments,
                "risk_level": migration_outcome["risk_level"],
                "migration_weight": migration_outcome["migration_weight"],
            }
            await self._record_migration_non_execute(
                context=context,
                tool_name=tool_name,
                capability_meta=capability_meta,
                invoke_arguments=invoke_arguments,
                output=observed,
                execution_mode="observe_only",
                migration_outcome=migration_outcome,
            )
            return observed

        if migration_outcome["decision"] == "require_approval":
            approval_result = await self._create_approval_request(
                context=context,
                tool_name=tool_name,
                invoke_arguments=invoke_arguments,
                migration_outcome=migration_outcome,
            )
            await self._record_migration_non_execute(
                context=context,
                tool_name=tool_name,
                capability_meta=capability_meta,
                invoke_arguments=invoke_arguments,
                output=approval_result,
                execution_mode="pending_approval",
                migration_outcome=migration_outcome,
            )
            return approval_result

        self._perf_metrics["tool_calls"] += 1
        observation = self._observe_tool(
            trace,
            "tool_execution",
            {"tool": tool_name, "run_id": context.run_id, "arguments": invoke_arguments},
        )
        start_ns = time.perf_counter_ns()
        try:
            result = await self.registry.invoke_handler(tool_name, **invoke_arguments)
            result, result_modifications = self._sanitize_tool_payload(
                result,
                source=f"tool_result:{tool_name}",
            )
            if result_modifications:
                self._security_audit.record(
                    event_type="tool_result_sanitized",
                    source="runtime",
                    details={
                        "tool_name": tool_name,
                        "run_id": context.run_id,
                        "modifications": result_modifications,
                    },
                )
            execution_time_ms = (time.perf_counter_ns() - start_ns) // 1_000_000
            if self._ledger is not None:
                task_type = (capability_meta.get("task_type") or "unknown") if capability_meta else "unknown"
                decision_reasoning = self._build_tool_decision_reasoning(capability_meta, context)
                await self._ledger.record_execution(
                    tenant_id=context.tenant_id,
                    agent_id=context.agent_id,
                    run_id=context.run_id,
                    capability_name=tool_name,
                    task_type=task_type,
                    input_params=invoke_arguments,
                    output_result=result if isinstance(result, dict) else {"result": result},
                    decision_reasoning=decision_reasoning,
                    execution_time_ms=execution_time_ms,
                    llm_model="",
                    llm_tokens_input=0,
                    llm_tokens_output=0,
                    estimated_cost=Decimal("0"),
                    status="success",
                    error_message=None,
                    migration_weight=migration_outcome["migration_weight"],
                    execution_mode="auto",
                    risk_level=Decimal(str(migration_outcome["risk_level"])),
                )
            self._finish_observation(
                observation,
                output=result if isinstance(result, dict) else {"result": result},
                status="success",
            )
            return result
        except ValueError:
            self._finish_observation(observation, status="error", output={"error": "not registered"})
            return {"error": f"Capability '{tool_name}' is not registered"}
        except Exception as exc:
            logger.exception("Tool '%s' failed", tool_name)
            safe_error = self._safe_error_message(exc)
            await self._notify_error(
                context=context,
                stage="tool_execution",
                category=self._classify_error(exc),
                message=f"{tool_name}: {safe_error}",
            )
            if self._ledger is not None:
                execution_time_ms = (time.perf_counter_ns() - start_ns) // 1_000_000
                task_type = (capability_meta.get("task_type") or "unknown") if capability_meta else "unknown"
                decision_reasoning = self._build_tool_decision_reasoning(capability_meta, context)
                try:
                    await self._ledger.record_execution(
                        tenant_id=context.tenant_id,
                        agent_id=context.agent_id,
                        run_id=context.run_id,
                        capability_name=tool_name,
                        task_type=task_type,
                        input_params=invoke_arguments,
                        output_result=None,
                        decision_reasoning=decision_reasoning,
                        execution_time_ms=execution_time_ms,
                        llm_model="",
                        llm_tokens_input=0,
                        llm_tokens_output=0,
                        estimated_cost=Decimal("0"),
                        status="error",
                        error_message=safe_error,
                        migration_weight=migration_outcome["migration_weight"],
                        execution_mode="auto",
                        risk_level=Decimal(str(migration_outcome["risk_level"])),
                    )
                except Exception as ledger_exc:
                    logger.exception("Ledger record_execution failed: %s", ledger_exc)
            self._finish_observation(
                observation,
                status="error",
                output={"error": _INTERNAL_ERROR_MESSAGE},
            )
            return {"error": _INTERNAL_ERROR_MESSAGE}

    def _enforce_tool_permissions(self, tool_name: str) -> str | None:
        security = self.config.get("security")
        if not isinstance(security, dict):
            return None
        allow = security.get("allow_tools")
        deny = security.get("deny_tools")
        normalized = tool_name.strip()
        if isinstance(allow, list):
            allow_set = {str(item).strip() for item in allow if str(item).strip()}
            if allow_set and normalized not in allow_set:
                return f"Tool '{normalized}' is not permitted"
        if isinstance(deny, list):
            deny_set = {str(item).strip() for item in deny if str(item).strip()}
            if normalized in deny_set:
                return f"Tool '{normalized}' is denied by policy"
        return None

    async def _enforce_rate_limit(self, tool_name: str) -> str | None:
        security = self.config.get("security")
        if not isinstance(security, dict):
            return None
        max_calls = security.get("max_tool_calls_per_minute")
        if isinstance(max_calls, bool):
            return None
        if not isinstance(max_calls, int | float | str):
            return None
        try:
            limit = int(max_calls)
        except (TypeError, ValueError):
            return None
        if limit < 1:
            return None
        async with self._tool_call_timestamps_lock:
            now = time.monotonic()
            cutoff = now - 60.0
            while self._tool_call_timestamps and self._tool_call_timestamps[0] < cutoff:
                self._tool_call_timestamps.popleft()
            if len(self._tool_call_timestamps) >= limit:
                return f"Rate limit exceeded for tool '{tool_name}'"
            self._tool_call_timestamps.append(now)
            return None

    def _get_skill_owlclaw_config(self, tool_name: str) -> dict[str, Any]:
        if self.registry is None:
            return {}
        try:
            skill = self.registry.skills_loader.get_skill(tool_name)
        except Exception:
            return {}
        if skill is None:
            return {}
        cfg = getattr(skill, "owlclaw_config", {})
        return cfg if isinstance(cfg, dict) else {}

    def _evaluate_migration(
        self,
        tool_name: str,
        invoke_arguments: dict[str, Any],
        skill_owlclaw: dict[str, Any],
    ) -> dict[str, Any]:
        fallback = {
            "decision": "auto_execute",
            "migration_weight": 100,
            "risk_level": 0.0,
            "execution_probability": 1.0,
        }
        gate = self._migration_gate
        if gate is None:
            return fallback
        action = self._build_migration_action(invoke_arguments)
        try:
            outcome = gate.evaluate(
                skill_name=tool_name,
                action=action,
                skill_owlclaw=skill_owlclaw,
            )
        except Exception:
            logger.debug("MigrationGate evaluation failed", exc_info=True)
            return fallback
        decision_value = getattr(outcome, "decision", "auto_execute")
        if hasattr(decision_value, "value"):
            decision_value = decision_value.value
        return {
            "decision": str(decision_value).strip().lower(),
            "migration_weight": int(getattr(outcome, "migration_weight", 100)),
            "risk_level": float(getattr(outcome, "risk_level", 0.0)),
            "execution_probability": float(getattr(outcome, "execution_probability", 1.0)),
        }

    @staticmethod
    def _build_migration_action(invoke_arguments: dict[str, Any]) -> dict[str, Any]:
        amount_raw = invoke_arguments.get("amount", invoke_arguments.get("total_amount", 0))
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            amount = 0.0

        scope = "single"
        for value in invoke_arguments.values():
            if isinstance(value, list | tuple):
                scope = "batch" if len(value) > 1 else scope
                break
        operation = str(invoke_arguments.get("operation_type", "write")).strip().lower() or "write"
        reversibility = str(invoke_arguments.get("reversibility", "reversible")).strip().lower() or "reversible"
        return {
            "action_type": operation,
            "impact_scope": scope,
            "amount": amount,
            "reversibility": reversibility,
        }

    async def _create_approval_request(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        invoke_arguments: dict[str, Any],
        migration_outcome: dict[str, Any],
    ) -> dict[str, Any]:
        queue = self._approval_queue
        if queue is None:
            return {
                "status": "pending_approval",
                "tool": tool_name,
                "request_id": None,
                "reason": "approval queue unavailable",
                "migration_weight": migration_outcome["migration_weight"],
            }
        request = await queue.create(
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            skill_name=tool_name,
            suggestion=invoke_arguments,
            reasoning=f"risk={migration_outcome['risk_level']:.4f}",
        )
        return {
            "status": "pending_approval",
            "tool": tool_name,
            "request_id": request.id,
            "migration_weight": migration_outcome["migration_weight"],
            "risk_level": migration_outcome["risk_level"],
            "execution_probability": migration_outcome["execution_probability"],
        }

    async def _record_migration_non_execute(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        capability_meta: dict[str, Any] | None,
        invoke_arguments: dict[str, Any],
        output: dict[str, Any],
        execution_mode: str,
        migration_outcome: dict[str, Any],
    ) -> None:
        if self._ledger is None:
            return
        task_type = (capability_meta.get("task_type") or "unknown") if capability_meta else "unknown"
        decision_reasoning = self._build_tool_decision_reasoning(capability_meta, context)
        try:
            await self._ledger.record_execution(
                tenant_id=context.tenant_id,
                agent_id=context.agent_id,
                run_id=context.run_id,
                capability_name=tool_name,
                task_type=task_type,
                input_params=invoke_arguments,
                output_result=output,
                decision_reasoning=decision_reasoning,
                execution_time_ms=0,
                llm_model="",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status="skipped" if execution_mode == "observe_only" else "pending",
                error_message=None,
                migration_weight=migration_outcome["migration_weight"],
                execution_mode=execution_mode,
                risk_level=Decimal(str(migration_outcome["risk_level"])),
            )
        except Exception:
            logger.debug("Failed to record migration non-execute event", exc_info=True)

    def _normalize_capability_arguments(
        self, arguments: dict[str, Any], context: AgentRunContext
    ) -> dict[str, Any]:
        """Normalize tool-call arguments before capability invocation.

        Supports both direct argument objects and legacy wrapped payloads:
        ``{"kwargs": {...}}``.

        When no arguments are provided, inject a default ``session`` object so
        handlers using the common ``handler(session)`` signature still work.
        """
        if "kwargs" in arguments and len(arguments) == 1 and isinstance(arguments["kwargs"], dict):
            normalized = dict(arguments["kwargs"])
        else:
            normalized = dict(arguments)

        if normalized:
            return normalized

        return {
            "session": {
                "agent_id": context.agent_id,
                "run_id": context.run_id,
                "trigger": context.trigger,
                "focus": context.focus,
                "payload": context.payload,
                "tenant_id": context.tenant_id,
            }
        }

    def _sanitize_tool_payload(
        self,
        payload: Any,
        *,
        source: str,
    ) -> tuple[Any, list[str]]:
        """Recursively sanitize untrusted text in tool payloads."""
        if isinstance(payload, str):
            sanitized = self._input_sanitizer.sanitize(payload, source=source)
            return sanitized.sanitized, list(sanitized.modifications)
        if isinstance(payload, dict):
            out: dict[Any, Any] = {}
            modifications: list[str] = []
            for key, value in payload.items():
                sanitized_value, mods = self._sanitize_tool_payload(value, source=source)
                out[key] = sanitized_value
                modifications.extend(mods)
            return out, modifications
        if isinstance(payload, list):
            out_list: list[Any] = []
            modifications: list[str] = []
            for item in payload:
                sanitized_item, mods = self._sanitize_tool_payload(item, source=source)
                out_list.append(sanitized_item)
                modifications.extend(mods)
            return out_list, modifications
        if isinstance(payload, tuple):
            out_tuple: list[Any] = []
            modifications: list[str] = []
            for item in payload:
                sanitized_item, mods = self._sanitize_tool_payload(item, source=source)
                out_tuple.append(sanitized_item)
                modifications.extend(mods)
            return tuple(out_tuple), modifications
        if isinstance(payload, set):
            out_set: set[Any] = set()
            modifications: list[str] = []
            for item in payload:
                sanitized_item, mods = self._sanitize_tool_payload(item, source=source)
                out_set.add(sanitized_item)
                modifications.extend(mods)
            return out_set, modifications
        return payload, []

    # ------------------------------------------------------------------
    # System prompt construction
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self,
        skills_context: str,
        visible_tools: list[dict[str, Any]],
    ) -> str:
        """Assemble the system prompt from identity, skills, and tool count."""
        assert self._identity_loader is not None
        identity = self._identity_loader.get_identity()

        parts: list[str] = []

        # Identity
        parts.append("# Your Identity\n")
        parts.append(identity["soul"])

        # Capabilities summary from IDENTITY.md
        if identity["capabilities_summary"]:
            parts.append("\n# Your Capabilities\n")
            parts.append(identity["capabilities_summary"])

        # Skills knowledge
        if skills_context:
            parts.append("\n# Business Knowledge\n")
            parts.append(skills_context)

        # Tool count hint
        if visible_tools:
            parts.append(
                f"\n# Available Tools\n"
                f"You have access to {len(visible_tools)} tools. "
                "Use function calling to choose actions.\n"
            )

        return "".join(parts)

    def _build_user_message(self, context: AgentRunContext) -> str:
        """Build the first user message from trigger context."""
        parts: list[str] = [f"Trigger: {context.trigger}"]

        if context.focus:
            parts.append(f"Focus: {context.focus}")

        if context.payload:
            parts.append(f"Context: {json.dumps(context.payload, default=str)}")

        raw_message = "\n".join(parts)
        sanitized = self._input_sanitizer.sanitize(raw_message, source=context.trigger)
        if sanitized.changed:
            self._security_audit.record(
                event_type="sanitization",
                source=context.trigger,
                details={
                    "agent_id": context.agent_id,
                    "run_id": context.run_id,
                    "modifications": sanitized.modifications,
                },
            )
        return sanitized.sanitized

    def _build_messages(
        self,
        context: AgentRunContext,
        skills_context: str,
        visible_tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build initial messages with strict system/user role separation."""
        user_message = self._build_user_message(context)
        context_window = self._resolve_context_window_limit(self.model)
        if context_window is None:
            system_prompt = self._build_system_prompt(skills_context, visible_tools)
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

        fixed_system_prompt = self._build_system_prompt("", visible_tools)
        fixed_tokens = (
            self._estimate_text_tokens(fixed_system_prompt)
            + self._estimate_text_tokens(user_message)
        )
        if fixed_tokens >= context_window:
            system_budget = max(1, context_window - 1)
            system_prompt = self._truncate_text_to_tokens(
                fixed_system_prompt,
                system_budget,
            )
            user_budget = max(
                0,
                context_window - self._estimate_text_tokens(system_prompt),
            )
            user_message = self._truncate_text_to_tokens(user_message, user_budget)
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

        skills_budget = context_window - fixed_tokens
        trimmed_skills_context = self._truncate_text_to_tokens(skills_context, skills_budget)
        system_prompt = self._build_system_prompt(trimmed_skills_context, visible_tools)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        if self._estimate_messages_tokens(messages) > context_window:
            overflow = self._estimate_messages_tokens(messages) - context_window
            reduced_budget = max(0, skills_budget - overflow)
            trimmed_skills_context = self._truncate_text_to_tokens(skills_context, reduced_budget)
            messages[0]["content"] = self._build_system_prompt(trimmed_skills_context, visible_tools)
        return messages

    def _build_skills_context(self, context: AgentRunContext) -> str:
        """Return Skills knowledge string, optionally filtered by focus."""
        if self.knowledge_injector is None or self.registry is None:
            return ""

        if self._run_handler_names_snapshot is not None:
            all_skill_names = list(self._run_handler_names_snapshot)
        else:
            all_skill_names = list(self.registry.handlers.keys())
        if not all_skill_names:
            return ""
        cache_key = (context.tenant_id, context.focus, tuple(sorted(all_skill_names)))
        cached = self._skills_context_cache.get(cache_key)
        if cached is not None:
            self._skills_context_cache.move_to_end(cache_key)
            self._perf_metrics["skills_cache_hits"] += 1
            return cached

        # Focus filter: if focus is set, prefer skills whose tag list includes it
        if context.focus:
            focused = [
                n for n in all_skill_names
                if self._skill_has_focus(n, context.focus)
            ]
            if not focused:
                return ""
            skill_names = focused
        else:
            skill_names = all_skill_names

        skill_token_budget = self._resolve_skill_token_budget()
        report = self.knowledge_injector.get_skills_knowledge_report(
            skill_names,
            max_tokens=skill_token_budget,
            focus=context.focus,
        )
        self._perf_metrics["skills_tokens_total"] = float(report.total_tokens)
        self._perf_metrics["skills_selected_count"] = float(len(report.selected_skill_names))
        self._perf_metrics["skills_dropped_count"] = float(len(report.dropped_skill_names))
        self._perf_metrics["skills_tokens_per_skill"] = float(
            sum(report.per_skill_tokens.values())
        )
        out = report.content
        self._skills_context_cache[cache_key] = out
        self._skills_context_cache.move_to_end(cache_key)
        while len(self._skills_context_cache) > 64:
            self._skills_context_cache.popitem(last=False)
        return out

    def _resolve_skill_token_budget(self) -> int | None:
        """Resolve skill prompt token budget from runtime config."""
        raw_values = [
            self.config.get("skills_token_limit"),
            self.config.get("skills_prompt_token_budget"),
        ]
        governance = self.config.get("governance")
        if isinstance(governance, dict):
            raw_values.append(governance.get("skills_token_limit"))
            raw_values.append(governance.get("skills_prompt_token_budget"))
        for raw in raw_values:
            if isinstance(raw, bool):
                continue
            if isinstance(raw, int) and raw > 0:
                return raw
            if isinstance(raw, str):
                stripped = raw.strip()
                if stripped.isdigit() and int(stripped) > 0:
                    return int(stripped)
        return None

    def _resolve_context_window_limit(self, model_name: str) -> int | None:
        raw_values = [
            self.config.get("context_window_tokens"),
            self.config.get("max_prompt_tokens"),
            self.config.get("prompt_context_window"),
        ]
        for raw in raw_values:
            if isinstance(raw, bool):
                continue
            if isinstance(raw, int) and raw > 0:
                return raw
            if isinstance(raw, str):
                stripped = raw.strip()
                if stripped.isdigit() and int(stripped) > 0:
                    return int(stripped)
        return _MODEL_CONTEXT_WINDOWS.get(model_name)

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)

    def _estimate_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        total = 0
        for message in messages:
            total += self._estimate_text_tokens(str(message.get("content", "")))
        return total

    def _truncate_text_to_tokens(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0 or not text:
            return ""
        if self._estimate_text_tokens(text) <= max_tokens:
            return text
        max_chars = max_tokens * _CHARS_PER_TOKEN
        return text[:max_chars]

    def _release_skill_content_cache(self) -> None:
        """Release cached full Skill contents and prompt cache after each run."""
        self._skills_context_cache.clear()
        if self.registry is None:
            return
        loader = getattr(self.registry, "skills_loader", None)
        if loader is None:
            return
        list_fn = getattr(loader, "list_skills", None)
        if not callable(list_fn):
            return
        try:
            for skill in list_fn():
                clear_fn = getattr(skill, "clear_full_content_cache", None)
                if callable(clear_fn):
                    clear_fn()
        except Exception:
            logger.debug("Failed to release Skill content cache", exc_info=True)

    def _capture_run_skill_snapshot(self) -> None:
        """Freeze skill/capability view for the current run."""
        if self.registry is None:
            self._run_handler_names_snapshot = tuple()
            self._run_capabilities_snapshot = []
            return
        try:
            self._run_handler_names_snapshot = tuple(sorted(str(name) for name in self.registry.handlers.keys()))
        except Exception:
            self._run_handler_names_snapshot = tuple()
        try:
            capabilities = self.registry.list_capabilities()
            self._run_capabilities_snapshot = list(capabilities) if isinstance(capabilities, list) else []
        except Exception:
            self._run_capabilities_snapshot = []

    def _clear_run_skill_snapshot(self) -> None:
        """Release per-run frozen skill/capability snapshot."""
        self._run_handler_names_snapshot = None
        self._run_capabilities_snapshot = None

    def _inject_skill_env_for_run(self) -> None:
        """Inject environment variables declared by skills for current run."""
        if self.registry is None:
            return
        loader = getattr(self.registry, "skills_loader", None)
        if loader is None:
            return
        get_skill = getattr(loader, "get_skill", None)
        if not callable(get_skill):
            return
        allowlist: set[str] = set(self.config.get("skill_env_allowlist") or [])
        skill_names = (
            list(self._run_handler_names_snapshot)
            if self._run_handler_names_snapshot is not None
            else list(getattr(self.registry, "handlers", {}).keys())
        )
        for skill_name in skill_names:
            try:
                skill = get_skill(skill_name)
            except Exception:
                logger.debug("Failed to resolve skill for env injection: %s", skill_name, exc_info=True)
                continue
            if skill is None:
                continue
            declared_env = getattr(skill, "owlclaw_config", {}).get("env")
            if not isinstance(declared_env, dict):
                continue
            for raw_key, raw_value in declared_env.items():
                if not isinstance(raw_key, str):
                    continue
                key = raw_key.strip()
                if not key:
                    continue
                if not key.startswith(_SKILL_ENV_PREFIX) and key not in allowlist:
                    logger.debug(
                        "Ignoring skill env key without %s prefix or allowlist: %s",
                        _SKILL_ENV_PREFIX,
                        key,
                    )
                    continue
                if key not in self._run_env_restore:
                    self._run_env_restore[key] = os.environ.get(key)
                os.environ[key] = str(raw_value)

    def _restore_skill_env_after_run(self) -> None:
        """Restore process env vars changed by _inject_skill_env_for_run."""
        if not self._run_env_restore:
            return
        for key, previous in self._run_env_restore.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        self._run_env_restore.clear()

    def _skill_has_focus(self, skill_name: str, focus: str) -> bool:
        """Return True if the skill declares *focus* in owlclaw.focus.

        Falls back to metadata.tags for backwards compatibility.
        """
        if self.registry is None:
            return False
        try:
            skill = self.registry.skills_loader.get_skill(skill_name)
        except Exception as exc:
            logger.warning("Failed to resolve skill focus metadata for %s: %s", skill_name, exc)
            return False
        if skill is None:
            return False
        target = focus.strip().lower()
        if not target:
            return False

        declared_focus = skill.owlclaw_config.get("focus", [])
        if isinstance(declared_focus, str):
            declared_focus = [declared_focus]
        if isinstance(declared_focus, list):
            normalized_focus = {str(item).strip().lower() for item in declared_focus if str(item).strip()}
            if normalized_focus:
                return target in normalized_focus

        tags = skill.metadata.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if isinstance(tags, list):
            normalized_tags = {str(item).strip().lower() for item in tags if str(item).strip()}
            return target in normalized_tags
        return False

    # ------------------------------------------------------------------
    # Visible tools
    # ------------------------------------------------------------------

    async def _get_visible_tools(
        self, context: AgentRunContext
    ) -> list[dict[str, Any]]:
        """Build the governance-filtered OpenAI-style function schema list."""
        confirmed = self._extract_confirmed_capabilities(context.payload)
        cache_key = (context.agent_id, context.tenant_id, tuple(sorted(confirmed)))
        cached_tools = self._visible_tools_cache.get(cache_key)
        if cached_tools is not None:
            self._visible_tools_cache.move_to_end(cache_key)
            self._perf_metrics["tools_cache_hits"] += 1
            return list(cached_tools)

        all_schemas: list[dict[str, Any]] = []

        if self.builtin_tools is not None:
            all_schemas.extend(self.builtin_tools.get_tool_schemas())

        if self.registry is not None:
            all_schemas.extend(self._capability_schemas())

        if self.visibility_filter is None:
            out = self._apply_tool_resource_limits(all_schemas)
            self._visible_tools_cache[cache_key] = list(out)
            self._visible_tools_cache.move_to_end(cache_key)
            while len(self._visible_tools_cache) > 64:
                self._visible_tools_cache.popitem(last=False)
            return out

        builtin_names = {s["function"]["name"] for s in all_schemas if self.builtin_tools and self.builtin_tools.is_builtin(s["function"]["name"])}
        cap_schemas = [s for s in all_schemas if s["function"]["name"] not in builtin_names]
        if not cap_schemas:
            return all_schemas

        # Use governance VisibilityFilter for capabilities only (with task_type/constraints from registry)
        from owlclaw.governance.visibility import CapabilityView, RunContext

        if self.registry is None:
            return all_schemas
        cap_list = (
            list(self._run_capabilities_snapshot)
            if self._run_capabilities_snapshot is not None
            else self.registry.list_capabilities()
        )
        name_to_meta = {c["name"]: c for c in cap_list}
        cap_views = [
            CapabilityView(
                name=s["function"]["name"],
                description=s["function"].get("description", ""),
                task_type=name_to_meta.get(s["function"]["name"], {}).get("task_type"),
                constraints=name_to_meta.get(s["function"]["name"], {}).get("constraints") or {},
                focus=name_to_meta.get(s["function"]["name"], {}).get("focus"),
                risk_level=name_to_meta.get(s["function"]["name"], {}).get("risk_level"),
                requires_confirmation=name_to_meta.get(s["function"]["name"], {}).get("requires_confirmation"),
            )
            for s in cap_schemas
        ]
        run_ctx = RunContext(
            tenant_id=context.tenant_id,
            confirmed_capabilities=confirmed or None,
        )
        visible_caps = await self.visibility_filter.filter_capabilities(
            cap_views, context.agent_id, run_ctx
        )
        visible_names = {cap.name for cap in visible_caps}
        filtered_caps = [s for s in cap_schemas if s["function"]["name"] in visible_names]
        out = all_schemas[: len(all_schemas) - len(cap_schemas)] + filtered_caps
        out = self._apply_tool_resource_limits(out)
        self._visible_tools_cache[cache_key] = list(out)
        self._visible_tools_cache.move_to_end(cache_key)
        while len(self._visible_tools_cache) > 64:
            self._visible_tools_cache.popitem(last=False)
        return out

    def _apply_tool_resource_limits(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        perf_cfg = self.config.get("performance")
        if not isinstance(perf_cfg, dict):
            return tools
        max_visible_tools = perf_cfg.get("max_visible_tools")
        if isinstance(max_visible_tools, bool):
            return tools
        if isinstance(max_visible_tools, int) and max_visible_tools >= 1:
            return tools[:max_visible_tools]
        return tools

    @staticmethod
    def _extract_confirmed_capabilities(payload: dict[str, Any]) -> set[str]:
        """Parse confirmed capabilities from payload in list/set/tuple/csv forms."""
        confirmed_raw = payload.get("confirmed_capabilities")
        if isinstance(confirmed_raw, list | tuple | set):
            out: set[str] = set()
            for name in confirmed_raw:
                if name is None:
                    continue
                normalized = str(name).strip()
                if normalized:
                    out.add(normalized)
            return out
        if isinstance(confirmed_raw, str):
            return {
                part.strip()
                for part in confirmed_raw.split(",")
                if part.strip()
            }
        return set()

    def _build_tool_decision_reasoning(
        self,
        capability_meta: dict[str, Any] | None,
        context: AgentRunContext,
    ) -> str:
        """Build compact audit payload for capability execution records."""
        meta = capability_meta or {}
        confirmed = self._extract_confirmed_capabilities(context.payload)
        payload = {
            "source": "runtime_tool_execution",
            "trigger": context.trigger,
            "focus": context.focus,
            "risk_level": _normalize_risk_level(meta.get("risk_level", "low")),
            "requires_confirmation": _coerce_confirmation_flag(meta.get("requires_confirmation", False)),
            "confirmed": meta.get("name") in confirmed if meta.get("name") else False,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _capability_schemas(self) -> list[dict[str, Any]]:
        """Convert registered capabilities to OpenAI function schemas."""
        if self.registry is None:
            return []

        schemas: list[dict[str, Any]] = []
        source = (
            list(self._run_capabilities_snapshot)
            if self._run_capabilities_snapshot is not None
            else self.registry.list_capabilities()
        )
        capabilities = sorted(source, key=lambda c: str(c.get("name", "")))
        for capability in capabilities:
            capability_name = str(capability.get("name", "")).strip()
            if not capability_name:
                continue
            schemas.append({
                "type": "function",
                "function": {
                    "name": capability_name,
                    "description": capability.get("description") or "",
                    "parameters": self._get_capability_parameters_schema(capability_name),
                },
            })
        return schemas

    def _get_capability_parameters_schema(self, capability_name: str) -> dict[str, Any]:
        """Build tool parameter schema using SKILL.md tools_schema when available."""
        default_schema: dict[str, Any] = {
            "type": "object",
            "description": "Arguments for this capability.",
            "additionalProperties": True,
            "required": [],
        }
        if self.registry is None:
            return default_schema
        try:
            skill = self.registry.skills_loader.get_skill(capability_name)
        except Exception:
            return default_schema
        if skill is None:
            return default_schema
        metadata = getattr(skill, "metadata", {})
        if not isinstance(metadata, dict):
            return default_schema
        tools_schema = metadata.get("tools_schema", {})
        if not isinstance(tools_schema, dict):
            return default_schema
        tool_def = tools_schema.get(capability_name)
        if not isinstance(tool_def, dict):
            return default_schema
        raw_parameters = tool_def.get("parameters")
        if not isinstance(raw_parameters, dict):
            return default_schema
        return self._normalize_parameter_schema(raw_parameters, default_schema)

    def _validate_tool_arguments(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        """Validate tool arguments against builtin or capability schema."""
        schema = self._find_tool_parameters_schema(tool_name)
        if schema is None:
            return None
        validation_error = self._validate_json_schema_value(arguments, schema, path="$")
        if validation_error is None:
            return None
        return f"Invalid arguments for tool '{tool_name}': {validation_error}"

    def _find_tool_parameters_schema(self, tool_name: str) -> dict[str, Any] | None:
        if self.builtin_tools is not None:
            for schema in self.builtin_tools.get_tool_schemas():
                if not isinstance(schema, dict):
                    continue
                function = schema.get("function")
                if not isinstance(function, dict):
                    continue
                if function.get("name") != tool_name:
                    continue
                parameters = function.get("parameters")
                if isinstance(parameters, dict):
                    return parameters
        for schema in self._capability_schemas():
            if not isinstance(schema, dict):
                continue
            function = schema.get("function")
            if not isinstance(function, dict):
                continue
            if function.get("name") != tool_name:
                continue
            parameters = function.get("parameters")
            if isinstance(parameters, dict):
                return parameters
        return None

    @staticmethod
    def _normalize_parameter_schema(raw_schema: dict[str, Any], default_schema: dict[str, Any]) -> dict[str, Any]:
        schema = dict(raw_schema)
        if schema.get("type") != "object":
            return dict(default_schema)

        properties_raw = schema.get("properties")
        properties: dict[str, Any] = {}
        if isinstance(properties_raw, dict):
            for key, value in properties_raw.items():
                if not isinstance(key, str):
                    continue
                if isinstance(value, dict):
                    properties[key] = dict(value)
                else:
                    properties[key] = {"type": "string"}
        schema["properties"] = properties

        required_raw = schema.get("required", [])
        required: list[str] = []
        if isinstance(required_raw, list):
            for item in required_raw:
                if isinstance(item, str) and item in properties:
                    required.append(item)
        schema["required"] = required

        additional = schema.get("additionalProperties")
        if not isinstance(additional, bool | dict):
            additional = False
        schema["additionalProperties"] = additional
        schema.setdefault("description", default_schema.get("description", "Arguments for this capability."))
        return schema

    @classmethod
    def _validate_json_schema_value(cls, value: Any, schema: dict[str, Any], *, path: str) -> str | None:
        schema_type = schema.get("type")
        if isinstance(schema_type, str):
            if not cls._matches_json_type(value, schema_type):
                return f"{path} expected type '{schema_type}'"
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and enum_values:
            if value not in enum_values:
                return f"{path} must be one of {enum_values}"

        if schema_type == "object":
            if not isinstance(value, dict):
                return f"{path} expected type 'object'"
            properties = schema.get("properties", {})
            properties_map = properties if isinstance(properties, dict) else {}
            required = schema.get("required", [])
            if isinstance(required, list):
                for key in required:
                    if isinstance(key, str) and key not in value:
                        return f"{path}.{key} is required"
            for key, item in value.items():
                key_path = f"{path}.{key}"
                prop_schema = properties_map.get(key)
                if isinstance(prop_schema, dict):
                    nested_error = cls._validate_json_schema_value(item, prop_schema, path=key_path)
                    if nested_error is not None:
                        return nested_error
                    continue
                additional = schema.get("additionalProperties", True)
                if additional is False:
                    return f"{key_path} is not allowed"
                if isinstance(additional, dict):
                    nested_error = cls._validate_json_schema_value(item, additional, path=key_path)
                    if nested_error is not None:
                        return nested_error
            return None

        if schema_type == "array":
            if not isinstance(value, list):
                return f"{path} expected type 'array'"
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    nested_error = cls._validate_json_schema_value(item, item_schema, path=f"{path}[{index}]")
                    if nested_error is not None:
                        return nested_error
            return None

        if schema_type == "string":
            if not isinstance(value, str):
                return f"{path} expected type 'string'"
            pattern = schema.get("pattern")
            if isinstance(pattern, str):
                try:
                    compiled = re.compile(pattern)
                except re.error:
                    compiled = None
                if compiled is not None and not compiled.search(value):
                    return f"{path} does not match required pattern"
            min_length = schema.get("minLength")
            if isinstance(min_length, int) and len(value) < min_length:
                return f"{path} length must be >= {min_length}"
            max_length = schema.get("maxLength")
            if isinstance(max_length, int) and len(value) > max_length:
                return f"{path} length must be <= {max_length}"
            return None

        if schema_type in {"integer", "number"}:
            if not cls._matches_json_type(value, schema_type):
                return f"{path} expected type '{schema_type}'"
            minimum = schema.get("minimum")
            if isinstance(minimum, int | float) and value < minimum:
                return f"{path} must be >= {minimum}"
            maximum = schema.get("maximum")
            if isinstance(maximum, int | float) and value > maximum:
                return f"{path} must be <= {maximum}"
            return None

        return None

    @staticmethod
    def _matches_json_type(value: Any, schema_type: str) -> bool:
        if schema_type == "object":
            return isinstance(value, dict)
        if schema_type == "array":
            return isinstance(value, list)
        if schema_type == "string":
            return isinstance(value, str)
        if schema_type == "boolean":
            return isinstance(value, bool)
        if schema_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if schema_type == "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        if schema_type == "null":
            return value is None
        return True
