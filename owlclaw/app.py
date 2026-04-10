"""OwlClaw main application class."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from starlette.applications import Starlette

from owlclaw.agent import AgentRuntime
from owlclaw.capabilities.bindings import BindingTool
from owlclaw.capabilities.knowledge import KnowledgeInjector
from owlclaw.capabilities.registry import CapabilityRegistry
from owlclaw.capabilities.skills import SkillsLoader, auto_register_binding_tools
from owlclaw.config import ConfigManager
from owlclaw.governance.visibility import CapabilityView
from owlclaw.integrations import llm as llm_integration
from owlclaw.security.sanitizer import InputSanitizer
from owlclaw.triggers.api import (
    APIKeyAuthProvider,
    APITriggerConfig,
    APITriggerRegistration,
    APITriggerServer,
    BearerTokenAuthProvider,
    GovernanceDecision,
)
from owlclaw.triggers.cron import CronTriggerRegistry
from owlclaw.triggers.db_change import (
    DBChangeTriggerConfig,
    DBChangeTriggerManager,
    DBChangeTriggerRegistration,
    PostgresNotifyAdapter,
)
from owlclaw.triggers.signal import AgentStateManager
from owlclaw.web.mount import mount_console

logger = logging.getLogger(__name__)


def _dict_to_capability_view(d: dict[str, Any]) -> CapabilityView:
    """Build CapabilityView from registry list_capabilities() item."""
    return CapabilityView(
        name=d.get("name", ""),
        description=d.get("description", ""),
        task_type=d.get("task_type"),
        constraints=d.get("constraints") or {},
        focus=d.get("focus") or [],
        risk_level=d.get("risk_level") or "low",
        requires_confirmation=d.get("requires_confirmation"),
    )


class _AllowAllGovernance:
    async def allow_trigger(self, event_name: str, tenant_id: str) -> bool:  # noqa: ARG002
        return True


class _APIGovernanceBridge:
    def __init__(self, app: OwlClaw) -> None:
        self._app = app

    async def evaluate_request(
        self,
        event_name: str,
        tenant_id: str,
        payload: dict[str, Any],  # noqa: ARG002
    ) -> GovernanceDecision:
        if self._app._governance_config is None:
            return GovernanceDecision(allowed=True)

        limits = self._app._governance_config.get("api_limits", {})
        if not isinstance(limits, dict):
            return GovernanceDecision(allowed=True)

        blocked_events = limits.get("blocked_events", [])
        if isinstance(blocked_events, list) and event_name in blocked_events:
            return GovernanceDecision(allowed=False, status_code=429, reason="rate_limited")

        blocked_tenants = limits.get("blocked_tenants", [])
        if isinstance(blocked_tenants, list) and tenant_id in blocked_tenants:
            return GovernanceDecision(allowed=False, status_code=503, reason="budget_exhausted")

        return GovernanceDecision(allowed=True)


class _RuntimeProxy:
    def __init__(self, app: OwlClaw) -> None:
        self._app = app

    async def trigger_event(
        self,
        event_name: str,
        payload: dict[str, Any],
        focus: str | None = None,
        tenant_id: str = "default",
    ) -> Any:
        if self._app._runtime is None:
            raise RuntimeError("Agent runtime is not started; call app.start() before consuming trigger events.")
        return await self._app._runtime.trigger_event(
            event_name=event_name,
            payload=payload,
            focus=focus,
            tenant_id=tenant_id,
        )


class OwlClaw:
    """OwlClaw application — the entry point for business applications.

    Usage::

        from owlclaw import OwlClaw

        app = OwlClaw("mionyee-trading")
        app.mount_skills("./capabilities/")

        @app.handler("entry-monitor")
        async def check_entry(session) -> dict:
            ...

        app.run()
    """

    def __init__(self, name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        self.name = name.strip()
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._states: dict[str, Callable[..., Any]] = {}
        self._skills_path: str | None = None
        self._config: dict[str, Any] = {}

        # Capabilities components (initialized by mount_skills)
        self.skills_loader: SkillsLoader | None = None
        self.registry: CapabilityRegistry | None = None
        self.knowledge_injector: KnowledgeInjector | None = None

        # Governance (optional; initialized when configure(governance=...) is used)
        self._governance_config: dict[str, Any] | None = None
        self._visibility_filter: Any = None
        self._router: Any = None
        self._ledger: Any = None

        # Cron triggers (Task 4.3)
        self.cron_registry: CronTriggerRegistry = CronTriggerRegistry(self)
        self.db_change_manager: DBChangeTriggerManager | None = None
        self.api_trigger_server: APITriggerServer | None = None
        self._runtime: AgentRuntime | None = None
        self._langchain_adapter: Any = None
        self._lite_mode: bool = False

    @classmethod
    def lite(
        cls,
        name: str,
        *,
        skills_path: str | None = None,
        mock_responses: dict[str, Any] | None = None,
        heartbeat_interval_minutes: int | float = 5,
        governance: dict[str, Any] | None = None,
    ) -> OwlClaw:
        """Create an OwlClaw instance in Lite Mode — zero external dependencies.

        Lite Mode auto-configures:
        - LLM: mock mode (no API key needed)
        - Memory: in-memory store + random embedder (no PostgreSQL)
        - Governance: in-memory ledger (budget/rate-limit/circuit-breaker all work)
        - Scheduler: no Hatchet required (cron triggers skipped)

        This is the fastest way to see OwlClaw in action::

            from owlclaw import OwlClaw

            app = OwlClaw.lite("demo")
            app.mount_skills("./skills/")

            @app.handler("check-inventory")
            async def check(session) -> dict:
                return {"action": "reorder", "sku": "WIDGET-42"}

            app.run()

        Args:
            name: Application name.
            skills_path: Path to skills directory. Call mount_skills() later if None.
            mock_responses: Custom mock LLM responses keyed by task_type.
                            Defaults to a generic "acknowledged" response.
            heartbeat_interval_minutes: Heartbeat interval (default 5 min).
            governance: Optional governance config dict. Defaults to sensible
                        limits (budget, rate-limit, circuit-breaker all enabled
                        with in-memory ledger).
        """
        app = cls(name)
        app._lite_mode = True
        app._ensure_logging()

        default_mock = mock_responses or {
            "default": {
                "content": "Acknowledged. No action required at this time.",
                "function_calls": [],
            },
        }

        lite_governance = governance or {
            "visibility": {
                "budget": {"high_cost_threshold": "1.0"},
                "circuit_breaker": {"failure_threshold": 5, "recovery_timeout": 300},
            },
            "router": {},
            "use_inmemory_ledger": True,
        }
        lite_governance.setdefault("use_inmemory_ledger", True)

        app.configure(
            heartbeat_interval_minutes=heartbeat_interval_minutes,
            integrations={
                "llm": {
                    "mock_mode": True,
                    "mock_responses": default_mock,
                    "model": "mock",
                },
            },
            memory={
                "store": "inmemory",
                "embedder": "random",
            },
            governance=lite_governance,
        )
        llm_integration.configure_mock(default_mock)

        if skills_path is not None:
            app.mount_skills(skills_path)

        logger.info(
            "OwlClaw '%s' created in Lite Mode (mock LLM, in-memory storage, "
            "no external dependencies)",
            name,
        )
        return app

    @staticmethod
    def _ensure_logging() -> None:
        """Install default logging only when the root logger has no handlers."""
        root_logger = logging.getLogger()
        if root_logger.handlers:
            return
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    def mount_skills(self, path: str) -> None:
        """Mount Skills from a business application directory.

        Scans the directory for SKILL.md files following the Agent Skills spec,
        loads their frontmatter metadata, and registers them as capabilities.

        Args:
            path: Path to capabilities directory containing SKILL.md files
        """
        if self.skills_loader is not None or self.registry is not None or self.knowledge_injector is not None:
            raise RuntimeError("mount_skills() already called")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")
        normalized_path = path.strip()
        self._skills_path = normalized_path

        # Initialize Skills Loader
        self.skills_loader = SkillsLoader(Path(normalized_path))
        skills = self.skills_loader.scan()

        # Initialize Registry and Knowledge Injector
        self.registry = CapabilityRegistry(self.skills_loader)
        self.knowledge_injector = KnowledgeInjector(self.skills_loader)
        registered_binding_tools = auto_register_binding_tools(
            self.skills_loader,
            self.registry,
            self._ledger,
        )

        logger.info("Loaded %d Skills from %s", len(skills), normalized_path)
        if registered_binding_tools:
            logger.info("Auto-registered %d binding tools", len(registered_binding_tools))

    def handler(
        self,
        skill_name: str,
        *,
        runnable: Any | None = None,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        input_transformer: Callable[[dict[str, Any]], Any] | None = None,
        output_transformer: Callable[[Any], Any] | None = None,
        fallback: str | None = None,
        retry_policy: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
        enable_tracing: bool = True,
    ) -> Callable:
        """Decorator to register a capability handler associated with a Skill.

        The handler function is called when the Agent decides to invoke this capability
        via function calling. The skill_name must match a loaded SKILL.md's `name` field.

        Args:
            skill_name: Name of the Skill this handler implements

        Raises:
            RuntimeError: If mount_skills() hasn't been called yet
        """

        if runnable is not None:
            if input_schema is None:
                raise ValueError("input_schema is required when runnable is provided")
            self.register_langchain_runnable(
                name=skill_name,
                runnable=runnable,
                description=description or f"LangChain runnable for {skill_name}",
                input_schema=input_schema,
                output_schema=output_schema,
                input_transformer=input_transformer,
                output_transformer=output_transformer,
                fallback=fallback,
                retry_policy=retry_policy,
                timeout_seconds=timeout_seconds,
                enable_tracing=enable_tracing,
            )

            def passthrough(fn: Callable) -> Callable:
                return fn

            return passthrough

        def decorator(fn: Callable) -> Callable:
            if not self.registry:
                raise RuntimeError(
                    "Must call mount_skills() before registering handlers"
                )
            existing = self.registry.handlers.get(skill_name)
            if isinstance(existing, BindingTool):
                self.registry.handlers.pop(skill_name, None)
            self.registry.register_handler(skill_name, fn)
            self._handlers[skill_name] = fn
            return fn

        return decorator

    def _get_langchain_adapter(self) -> Any:
        """Build and cache LangChainAdapter using current app config."""
        if self._langchain_adapter is not None:
            return self._langchain_adapter

        from owlclaw.integrations.langchain import LangChainAdapter, LangChainConfig

        integrations_cfg = self._config.get("integrations", {})
        langchain_cfg: dict[str, Any] = {}
        if isinstance(integrations_cfg, dict):
            candidate = integrations_cfg.get("langchain")
            if isinstance(candidate, dict):
                langchain_cfg = candidate
        if not langchain_cfg:
            candidate_root = self._config.get("langchain")
            if isinstance(candidate_root, dict):
                langchain_cfg = candidate_root

        config = LangChainConfig.model_validate(langchain_cfg or {})
        config.validate_semantics()
        self._langchain_adapter = LangChainAdapter(self, config)
        return self._langchain_adapter

    def register_langchain_runnable(
        self,
        *,
        name: str,
        runnable: Any,
        description: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
        input_transformer: Callable[[dict[str, Any]], Any] | None = None,
        output_transformer: Callable[[Any], Any] | None = None,
        fallback: str | None = None,
        retry_policy: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
        enable_tracing: bool = True,
    ) -> None:
        """Register LangChain runnable into capability registry."""
        if not self.registry:
            raise RuntimeError("Must call mount_skills() before registering LangChain runnables")
        adapter = self._get_langchain_adapter()
        from owlclaw.integrations.langchain import RunnableConfig, check_langchain_version

        check_langchain_version()

        adapter.register_runnable(
            runnable=runnable,
            config=RunnableConfig(
                name=name,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
                input_transformer=input_transformer,
                output_transformer=output_transformer,
                fallback=fallback,
                retry_policy=retry_policy,
                timeout_seconds=timeout_seconds,
                enable_tracing=enable_tracing,
            ),
        )

    def cron(
        self,
        expression: str,
        *,
        event_name: str | None = None,
        focus: str | None = None,
        description: str | None = None,
        fallback: Callable | None = None,
        **kwargs: Any,
    ) -> Callable:
        """Decorator to register a function as a cron-triggered task.

        The decorated function becomes the default fallback handler unless
        *fallback* is explicitly provided.  If *event_name* is omitted, the
        function's ``__name__`` is used.

        Args:
            expression: 5-field cron expression (e.g. ``"0 9 * * 1-5"``).
            event_name: Unique identifier for this trigger.  Defaults to the
                decorated function's ``__name__``.
            focus: Optional focus tag that narrows which Skills the Agent loads
                when this cron fires.
            description: Human-readable description stored in the config.
            fallback: Explicit fallback callable.  If omitted, the decorated
                function itself is used as the fallback.
            **kwargs: Additional CronTriggerConfig fields such as
                ``max_daily_runs``, ``cooldown_seconds``, ``migration_weight``,
                ``priority``, etc.

        Returns:
            The original function (decorator is transparent).

        Raises:
            ValueError: If the cron expression is invalid or *event_name* is
                already registered.

        Example::

            @app.cron("0 9 * * 1-5", focus="market_open", max_daily_runs=1)
            async def morning_decision():
                \"\"\"Run every weekday at 09:00.\"\"\"
                ...
        """
        from functools import wraps

        def decorator(fn: Callable) -> Callable:
            name = event_name if event_name is not None else fn.__name__
            handler = fallback if fallback is not None else fn

            self.cron_registry.register(
                event_name=name,
                expression=expression,
                focus=focus,
                fallback_handler=handler,
                description=description or (fn.__doc__ or "").strip() or None,
                **kwargs,
            )

            @wraps(fn)
            async def wrapper(*args: Any, **kw: Any) -> Any:
                if inspect.iscoroutinefunction(fn):
                    return await fn(*args, **kw)
                return fn(*args, **kw)

            return wrapper

        return decorator

    def db_change(
        self,
        *,
        channel: str,
        event_name: str | None = None,
        tenant_id: str = "default",
        debounce_seconds: float | None = None,
        batch_size: int | None = None,
        max_buffer_events: int = 1000,
        max_payload_bytes: int = 7900,
        focus: str | None = None,
    ) -> Callable:
        """Decorator to register a db-change trigger with fallback handler."""

        from functools import wraps

        def decorator(fn: Callable) -> Callable:
            cfg = DBChangeTriggerConfig(
                tenant_id=tenant_id,
                channel=channel,
                event_name=event_name or fn.__name__,
                agent_id=self.name,
                debounce_seconds=debounce_seconds,
                batch_size=batch_size,
                max_buffer_events=max_buffer_events,
                max_payload_bytes=max_payload_bytes,
                focus=focus,
            )
            self._ensure_db_change_manager().register(cfg, handler=fn)

            @wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                if inspect.iscoroutinefunction(fn):
                    return await fn(*args, **kwargs)
                return fn(*args, **kwargs)

            return wrapper

        return decorator

    def api(
        self,
        *,
        path: str,
        method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST",
        event_name: str | None = None,
        tenant_id: str = "default",
        response_mode: Literal["sync", "async"] = "async",
        sync_timeout_seconds: int = 60,
        focus: str | None = None,
        auth_required: bool = True,
        description: str | None = None,
    ) -> Callable:
        """Decorator to register an API trigger endpoint with fallback handler."""

        from functools import wraps

        def decorator(fn: Callable) -> Callable:
            config = APITriggerConfig(
                path=path,
                method=cast(Literal["GET", "POST", "PUT", "PATCH", "DELETE"], method.upper()),
                event_name=event_name or fn.__name__,
                tenant_id=tenant_id,
                response_mode=response_mode,
                sync_timeout_seconds=sync_timeout_seconds,
                focus=focus,
                auth_required=auth_required,
                description=description or (fn.__doc__ or "").strip() or None,
            )
            self._ensure_api_trigger_server().register(config, fallback=fn)

            @wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                if inspect.iscoroutinefunction(fn):
                    return await fn(*args, **kwargs)
                return fn(*args, **kwargs)

            return wrapper

        return decorator

    def trigger(self, registration: DBChangeTriggerRegistration | APITriggerRegistration) -> None:
        """Register a trigger using function-call style API."""

        if isinstance(registration, DBChangeTriggerRegistration):
            self._ensure_db_change_manager().register(registration.config)
            return
        if isinstance(registration, APITriggerRegistration):
            self._ensure_api_trigger_server().register(registration.config)
            return
        raise TypeError(f"Unsupported trigger registration type: {type(registration).__name__}")

    def _ensure_db_change_manager(self) -> DBChangeTriggerManager:
        if self.db_change_manager is not None:
            return self.db_change_manager
        dsn = self._resolve_db_change_dsn()
        adapter = PostgresNotifyAdapter(dsn=dsn, reconnect_interval=self._resolve_db_change_reconnect_interval())
        self.db_change_manager = DBChangeTriggerManager(
            adapter=adapter,
            governance=_AllowAllGovernance(),
            agent_runtime=_RuntimeProxy(self),
            ledger=self if self._ledger is not None else None,
        )
        return self.db_change_manager

    def _resolve_db_change_dsn(self) -> str:
        triggers_cfg = self._config.get("triggers", {})
        db_change_cfg: dict[str, Any] = {}
        if isinstance(triggers_cfg, dict):
            candidate = triggers_cfg.get("db_change")
            if isinstance(candidate, dict):
                db_change_cfg = candidate
        dsn = db_change_cfg.get("dsn") or db_change_cfg.get("database_url") or os.getenv("OWLCLAW_DATABASE_URL")
        if not isinstance(dsn, str) or not dsn.strip():
            raise RuntimeError(
                "db_change requires database dsn; set triggers.db_change.dsn or OWLCLAW_DATABASE_URL before registration"
            )
        return dsn.strip()

    def _resolve_db_change_reconnect_interval(self) -> float:
        triggers_cfg = self._config.get("triggers", {})
        if isinstance(triggers_cfg, dict):
            db_change_cfg = triggers_cfg.get("db_change")
            if isinstance(db_change_cfg, dict):
                value = db_change_cfg.get("reconnect_interval", 30.0)
                if isinstance(value, int | float) and value > 0:
                    return float(value)
        return 30.0

    def _ensure_api_trigger_server(self) -> APITriggerServer:
        if self.api_trigger_server is not None:
            return self.api_trigger_server
        config = self._resolve_api_trigger_config()
        auth_provider = self._build_api_auth_provider(config)
        sanitizer = InputSanitizer() if config.get("sanitize_input", True) else None
        governance_gate = _APIGovernanceBridge(self)
        self.api_trigger_server = APITriggerServer(
            host=str(config.get("host", "0.0.0.0")),
            port=int(config.get("port", 8080)),
            auth_provider=auth_provider,
            agent_runtime=_RuntimeProxy(self),
            governance_gate=governance_gate,
            sanitizer=sanitizer,
            ledger=self if self._ledger is not None else None,
            agent_id=self.name,
            max_body_bytes=int(config.get("max_body_bytes", 1024 * 1024)),
            cors_origins=list(config.get("cors_origins", ["*"])),
            tenant_rate_limit_per_minute=int(config.get("tenant_rate_limit_per_minute", 120)),
            endpoint_rate_limit_per_minute=int(config.get("endpoint_rate_limit_per_minute", 60)),
        )
        return self.api_trigger_server

    def _resolve_api_trigger_config(self) -> dict[str, Any]:
        triggers_cfg = self._config.get("triggers", {})
        if isinstance(triggers_cfg, dict):
            api_cfg = triggers_cfg.get("api")
            if isinstance(api_cfg, dict):
                return dict(api_cfg)
        return {}

    @staticmethod
    def _build_api_auth_provider(config: dict[str, Any]) -> APIKeyAuthProvider | BearerTokenAuthProvider | None:
        auth_type = str(config.get("auth_type", "none")).strip().lower()
        if auth_type == "api_key":
            keys = config.get("api_keys", [])
            if isinstance(keys, list):
                return APIKeyAuthProvider({str(item) for item in keys if str(item).strip()})
            return APIKeyAuthProvider(set())
        if auth_type == "bearer":
            tokens = config.get("bearer_tokens", [])
            if isinstance(tokens, list):
                return BearerTokenAuthProvider({str(item) for item in tokens if str(item).strip()})
            return BearerTokenAuthProvider(set())
        return None

    def state(self, name: str) -> Callable:
        """Decorator to register a state provider.

        State providers are called by the Agent's query_state built-in tool
        to get current business state snapshots.

        Args:
            name: Name of the state this provider supplies

        Raises:
            RuntimeError: If mount_skills() hasn't been called yet
        """

        def decorator(fn: Callable) -> Callable:
            if not self.registry:
                raise RuntimeError(
                    "Must call mount_skills() before registering states"
                )

            self.registry.register_state(name, fn)
            self._states[name] = fn
            return fn

        return decorator

    def configure(self, **kwargs: Any) -> None:
        """Configure Agent identity, heartbeat, governance, etc.

        Accepts: soul, identity, heartbeat_interval_minutes, governance (dict), and other Agent config.
        """
        if self._runtime is not None:
            raise RuntimeError("configure() cannot be called after app.start(); stop runtime first")
        nested_overrides = self._to_nested_overrides(kwargs)
        manager = ConfigManager.load(overrides=nested_overrides)
        self._config = manager.get().model_dump(mode="python")
        triggers_cfg = self._config.get("triggers")
        if isinstance(triggers_cfg, dict):
            self.cron_registry.apply_settings(triggers_cfg)
        governance_cfg = nested_overrides.get("governance")
        if isinstance(governance_cfg, dict):
            self._governance_config = governance_cfg

    @staticmethod
    def _to_nested_overrides(kwargs: dict[str, Any]) -> dict[str, Any]:
        """Convert flat app.configure kwargs into nested config overrides."""
        shortcut_paths = {
            "soul": ("agent", "soul"),
            "identity": ("agent", "identity"),
            "heartbeat_interval_minutes": ("agent", "heartbeat_interval_minutes"),
            "max_iterations": ("agent", "max_iterations"),
            "model": ("integrations", "llm", "model"),
            "temperature": ("integrations", "llm", "temperature"),
            "fallback_models": ("integrations", "llm", "fallback_models"),
        }
        top_level_sections = {
            "agent",
            "governance",
            "triggers",
            "integrations",
            "security",
            "memory",
        }

        nested: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key in top_level_sections and isinstance(value, dict):
                current = nested.get(key, {})
                if isinstance(current, dict):
                    current.update(value)
                    nested[key] = current
                else:
                    nested[key] = value
                continue

            path: tuple[str, ...]
            if key in shortcut_paths:
                path = shortcut_paths[key]
            elif "__" in key:
                path = tuple(part.strip().lower() for part in key.split("__") if part.strip())
                if not path:
                    continue
            else:
                path = ("agent", key)

            cursor = nested
            for part in path[:-1]:
                existing = cursor.get(part)
                if not isinstance(existing, dict):
                    existing = {}
                    cursor[part] = existing
                cursor = existing
            cursor[path[-1]] = value
        return nested

    def _ensure_governance(self) -> None:
        """Create VisibilityFilter, Router, Ledger from _governance_config if not yet created."""
        if self._governance_config is None or self._visibility_filter is not None:
            return
        from owlclaw.governance import (
            BudgetConstraint,
            CircuitBreakerConstraint,
            InMemoryLedger,
            Ledger,
            RateLimitConstraint,
            RiskConfirmationConstraint,
            Router,
            TimeConstraint,
            VisibilityFilter,
        )

        cfg = self._governance_config
        fail_policy = cfg.get("fail_policy", "close")
        self._visibility_filter = VisibilityFilter(fail_policy=str(fail_policy))

        time_cfg = (cfg.get("visibility") or {}).get("time") or {}
        self._visibility_filter.register_evaluator(TimeConstraint(time_cfg))
        risk_cfg = (cfg.get("visibility") or {}).get("risk_confirmation") or {}
        self._visibility_filter.register_evaluator(
            RiskConfirmationConstraint(risk_cfg)
        )

        session_factory = cfg.get("session_factory")
        use_inmemory = cfg.get("use_inmemory_ledger", False) or self._lite_mode
        ledger: Ledger | InMemoryLedger | None = None

        if session_factory is not None:
            ledger = Ledger(
                session_factory,
                batch_size=cfg.get("ledger", {}).get("batch_size", 10)
                if isinstance(cfg.get("ledger"), dict)
                else 10,
                flush_interval=cfg.get("ledger", {}).get("flush_interval", 5.0)
                if isinstance(cfg.get("ledger"), dict)
                else 5.0,
                fallback_log_path=cfg.get("ledger", {}).get("fallback_log_path", "ledger_fallback.log")
                if isinstance(cfg.get("ledger"), dict)
                else "ledger_fallback.log",
            )
        elif use_inmemory:
            ledger = InMemoryLedger()

        if ledger is not None:
            self._ledger = ledger
            constraint_ledger = cast(Ledger, ledger)
            budget_cfg = (cfg.get("visibility") or {}).get("budget") or {}
            self._visibility_filter.register_evaluator(
                BudgetConstraint(constraint_ledger, budget_cfg)
            )
            self._visibility_filter.register_evaluator(RateLimitConstraint(constraint_ledger))
            cb_cfg = (cfg.get("visibility") or {}).get("circuit_breaker") or {}
            self._visibility_filter.register_evaluator(
                CircuitBreakerConstraint(constraint_ledger, cb_cfg)
            )

        router_cfg = cfg.get("router") or {}
        self._router = Router(router_cfg, default_model=self._resolve_runtime_model())

    async def get_visible_capabilities(
        self,
        agent_id: str,
        tenant_id: str = "default",
        confirmed_capabilities: list[str] | str | None = None,
    ) -> list[dict[str, Any]]:
        """Return capabilities visible after governance filtering (for use in Agent Run).

        Converts registry list to CapabilityView, runs VisibilityFilter, returns
        list of dicts (name, description, task_type, constraints) for visible capabilities.
        """
        if not self.registry:
            return []
        self._ensure_governance()
        raw = self.registry.list_capabilities()
        views = [_dict_to_capability_view(d) for d in raw]
        if self._visibility_filter is None:
            filtered = views
        else:
            from owlclaw.governance.visibility import RunContext

            confirmed: set[str] = set()
            if isinstance(confirmed_capabilities, list):
                confirmed = {
                    c.strip()
                    for c in confirmed_capabilities
                    if isinstance(c, str) and c.strip()
                }
            elif isinstance(confirmed_capabilities, str):
                confirmed = {
                    c.strip()
                    for c in confirmed_capabilities.split(",")
                    if c.strip()
                }
            ctx = RunContext(
                tenant_id=tenant_id,
                confirmed_capabilities=confirmed or None,
            )
            filtered = await self._visibility_filter.filter_capabilities(
                views, agent_id, ctx
            )
            logger.info(
                "VisibilityFilter: %d of %d capabilities visible for agent %s",
                len(filtered),
                len(raw),
                agent_id,
            )
        return [
            {
                "name": c.name,
                "description": c.description,
                "task_type": c.task_type,
                "constraints": c.constraints,
                "focus": c.focus,
                "risk_level": c.risk_level,
                "requires_confirmation": c.requires_confirmation,
            }
            for c in filtered
        ]

    async def get_model_selection(
        self,
        task_type: str,
        tenant_id: str = "default",
    ) -> Any:
        """Return model and fallback chain for the given task_type (for use before LLM call)."""
        self._ensure_governance()
        if self._router is None:
            return None
        from owlclaw.governance.visibility import RunContext

        ctx = RunContext(tenant_id=tenant_id)
        return await self._router.select_model(task_type, ctx)

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
        estimated_cost: Any,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """Record one capability execution (for use after capability runs)."""
        self._ensure_governance()
        if self._ledger is None:
            return
        await self._ledger.record_execution(
            tenant_id=tenant_id,
            agent_id=agent_id,
            run_id=run_id,
            capability_name=capability_name,
            task_type=task_type,
            input_params=input_params,
            output_result=output_result,
            decision_reasoning=decision_reasoning,
            execution_time_ms=execution_time_ms,
            llm_model=llm_model,
            llm_tokens_input=llm_tokens_input,
            llm_tokens_output=llm_tokens_output,
            estimated_cost=estimated_cost,
            status=status,
            error_message=error_message,
        )

    async def start_governance(self) -> None:
        """Start governance background tasks (e.g. Ledger writer). Call before run()."""
        self._ensure_governance()
        if self._ledger is not None:
            await self._ledger.start()

    async def stop_governance(self) -> None:
        """Stop governance background tasks. Call on shutdown."""
        if self._ledger is not None:
            await self._ledger.stop()

    def create_agent_runtime(
        self,
        app_dir: str | None = None,
        hatchet_client: Any = None,
    ) -> AgentRuntime:
        """Create an AgentRuntime configured with this app's registry, governance, and built-in tools.

        Must be called after mount_skills(). If *app_dir* is omitted, uses the parent
        directory of the mounted skills path (where SOUL.md and IDENTITY.md are expected).
        If *hatchet_client* is provided, built-in schedule_once and cancel_schedule work.

        Returns:
            AgentRuntime configured with registry, knowledge_injector, visibility_filter,
            ledger, and BuiltInTools (query_state, log_decision).
        """
        if not self.registry or not self.knowledge_injector:
            raise RuntimeError(
                "Must call mount_skills() before create_agent_runtime()"
            )
        from owlclaw.agent import AgentRuntime, BuiltInTools

        resolved_app_dir: str
        if app_dir is not None:
            if not isinstance(app_dir, str) or not app_dir.strip():
                raise ValueError("app_dir must be a non-empty string when provided")
            resolved_app_dir = app_dir.strip()
        elif self._skills_path:
            resolved_app_dir = str(Path(self._skills_path).resolve().parent)
        else:
            raise RuntimeError(
                "Cannot determine app_dir: provide app_dir explicitly or call mount_skills() first"
            )

        self._ensure_governance()
        builtin_tools = BuiltInTools(
            capability_registry=self.registry,
            ledger=self._ledger,
            hatchet_client=hatchet_client,
        )
        signal_cfg: dict[str, Any] = {}
        triggers_cfg = self._config.get("triggers")
        if isinstance(triggers_cfg, dict):
            candidate = triggers_cfg.get("signal")
            if isinstance(candidate, dict):
                signal_cfg = candidate
        max_pending = signal_cfg.get("max_pending_instructions", 10)
        try:
            max_pending_instructions = max(1, int(max_pending))
        except (TypeError, ValueError):
            max_pending_instructions = 10
        signal_state_manager = AgentStateManager(max_pending_instructions=max_pending_instructions)
        runtime_config: dict[str, Any] = {}
        if self._lite_mode:
            runtime_config["heartbeat"] = {"enabled": False}
        integrations_cfg = self._config.get("integrations")
        if isinstance(integrations_cfg, dict):
            llm_cfg = integrations_cfg.get("llm")
            if isinstance(llm_cfg, dict):
                runtime_config["llm"] = dict(llm_cfg)
        runtime_model = self._resolve_runtime_model()
        return AgentRuntime(
            agent_id=self.name,
            app_dir=resolved_app_dir,
            registry=self.registry,
            knowledge_injector=self.knowledge_injector,
            visibility_filter=self._visibility_filter,
            builtin_tools=builtin_tools,
            router=self._router,
            ledger=self._ledger,
            signal_state_manager=signal_state_manager,
            model=runtime_model,
            config=runtime_config or None,
        )

    def _resolve_runtime_model(self) -> str:
        integrations_cfg = self._config.get("integrations")
        if isinstance(integrations_cfg, dict):
            llm_cfg = integrations_cfg.get("llm")
            if isinstance(llm_cfg, dict):
                configured_model = llm_cfg.get("model")
                if isinstance(configured_model, str) and configured_model.strip():
                    return configured_model.strip()
        return "gpt-4o-mini"

    async def start(
        self,
        *,
        app_dir: str | None = None,
        hatchet_client: Any = None,
        tenant_id: str = "default",
    ) -> AgentRuntime:
        """Start runtime + governance + trigger registration in embedded mode.

        This API is intended for service-style integration (`await app.start()`).
        It does not create a background heartbeat loop. Callers must schedule
        heartbeat externally (for example Hatchet Cron, Kubernetes CronJob, or
        their own scheduler that periodically calls
        `runtime.trigger_event("heartbeat", ...)`).

        Use `run()` when you need OwlClaw to manage its own blocking lifecycle
        with a built-in heartbeat loop.
        """
        if self._runtime is not None and self._runtime.is_initialized:
            logger.info("OwlClaw '%s' already started; reusing existing runtime", self.name)
            return self._runtime
        runtime = self.create_agent_runtime(app_dir=app_dir, hatchet_client=hatchet_client)
        try:
            await runtime.setup()
            self._runtime = runtime
            await self.start_governance()
            if hatchet_client is not None:
                self.cron_registry.start(
                    hatchet_client,
                    agent_runtime=runtime,
                    ledger=self._ledger,
                    tenant_id=tenant_id,
                )
            if self.db_change_manager is not None:
                await self.db_change_manager.start()
            if self.api_trigger_server is not None:
                await self.api_trigger_server.start()
            return runtime
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop governance and wait for cron in-flight tasks."""
        if self.api_trigger_server is not None:
            await self.api_trigger_server.stop()
        if self.db_change_manager is not None:
            await self.db_change_manager.stop()
        await self.stop_governance()
        await self.cron_registry.wait_for_all_tasks()
        self._runtime = None
        llm_integration.configure_mock(None)

    def health_status(self) -> dict[str, Any]:
        """Return app-level health summary."""
        db_change_channels = (
            self.db_change_manager.registered_channels_count
            if self.db_change_manager is not None
            else 0
        )
        api_registered_endpoints = (
            self.api_trigger_server.registered_endpoints_count
            if self.api_trigger_server is not None
            else 0
        )
        return {
            "app": self.name,
            "runtime_initialized": bool(self._runtime and self._runtime.is_initialized),
            "cron": self.cron_registry.get_health_status(),
            "db_change_registered_channels": db_change_channels,
            "api_registered_endpoints": api_registered_endpoints,
            "governance_enabled": self._ledger is not None,
        }

    def create_http_app(self) -> Starlette:
        """Create a Starlette host app and attempt to mount console routes."""
        app = Starlette(routes=[])
        mounted = mount_console(app)
        logger.info("Console mount status: %s", "enabled" if mounted else "disabled")
        return app

    def langchain_health_status(self) -> dict[str, Any]:
        """Return LangChain integration health summary."""
        adapter = self._get_langchain_adapter()
        return cast(dict[str, Any], adapter.health_status())

    def langchain_metrics(self, format: str = "json") -> dict[str, Any] | str:
        """Export LangChain metrics in JSON or Prometheus format."""
        adapter = self._get_langchain_adapter()
        return cast(dict[str, Any] | str, adapter.metrics(format=format))

    def run(
        self,
        *,
        app_dir: str | None = None,
        hatchet_client: Any = None,
        tenant_id: str = "default",
    ) -> None:
        """Start the OwlClaw application in standalone blocking mode.

        Initializes the Agent runtime, loads Skills, starts governance,
        registers triggers, and blocks until SIGINT/SIGTERM.

        Unlike `start()`, `run()` manages an internal heartbeat loop based on
        `heartbeat_interval_minutes`. The heartbeat task is cancelled and
        cleaned up automatically during shutdown.

        Args:
            app_dir: Application directory for identity files (SOUL.md, etc.).
                     Defaults to parent of the mounted skills path.
            hatchet_client: Optional pre-configured HatchetClient for durable
                            execution and cron scheduling.
            tenant_id: Tenant identifier for multi-tenant deployments.
        """
        self._ensure_logging()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            raise RuntimeError(
                "OwlClaw.run() cannot be called from an already-running event loop. "
                "Use 'await app.start()' and 'await app.stop()' directly instead."
            )

        asyncio.run(self._run_blocking(
            app_dir=app_dir,
            hatchet_client=hatchet_client,
            tenant_id=tenant_id,
        ))

    def run_once(
        self,
        *,
        event_name: str = "manual",
        payload: dict[str, Any] | None = None,
        focus: str | None = None,
        app_dir: str | None = None,
        hatchet_client: Any = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        """Run one trigger event through runtime decision loop and return structured result."""
        self._ensure_logging()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            raise RuntimeError(
                "OwlClaw.run_once() cannot be called from an already-running event loop. "
                "Use 'await app._run_once_async(...)' directly instead."
            )

        return asyncio.run(
            self._run_once_async(
                event_name=event_name,
                payload=payload,
                focus=focus,
                app_dir=app_dir,
                hatchet_client=hatchet_client,
                tenant_id=tenant_id,
            )
        )

    async def _run_once_async(
        self,
        *,
        event_name: str,
        payload: dict[str, Any] | None,
        focus: str | None,
        app_dir: str | None,
        hatchet_client: Any,
        tenant_id: str,
    ) -> dict[str, Any]:
        runtime = await self.start(
            app_dir=app_dir,
            hatchet_client=hatchet_client,
            tenant_id=tenant_id,
        )
        logger.info(
            "Run-once trigger started app=%s trigger=%s focus=%s tenant_id=%s",
            self.name,
            event_name,
            focus,
            tenant_id,
        )
        try:
            result = await runtime.trigger_event(
                event_name=event_name,
                payload=dict(payload or {}),
                focus=focus,
                tenant_id=tenant_id,
            )
        finally:
            await self.stop()

        structured_result = {
            "status": result.get("status", "unknown"),
            "run_id": result.get("run_id"),
            "trigger": event_name,
            "decision": {
                "iterations": result.get("iterations", 0),
                "tool_calls_total": result.get("tool_calls_total", 0),
                "final_response": result.get("final_response"),
                "reason": result.get("reason"),
            },
            "raw": result,
        }
        logger.info(
            "Run-once trigger finished app=%s trigger=%s status=%s tool_calls_total=%s",
            self.name,
            event_name,
            structured_result["status"],
            structured_result["decision"]["tool_calls_total"],
        )
        return structured_result

    async def _run_blocking(
        self,
        *,
        app_dir: str | None = None,
        hatchet_client: Any = None,
        tenant_id: str = "default",
    ) -> None:
        """Internal async entry point for the blocking run() method."""
        shutdown_event = asyncio.Event()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received — stopping OwlClaw '%s'", self.name)
            shutdown_event.set()

        loop = asyncio.get_running_loop()
        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _signal_handler)
        else:
            signal.signal(signal.SIGINT, lambda *_: _signal_handler())
            signal.signal(signal.SIGTERM, lambda *_: _signal_handler())

        logger.info("Starting OwlClaw application '%s'", self.name)

        runtime = await self.start(
            app_dir=app_dir,
            hatchet_client=hatchet_client,
            tenant_id=tenant_id,
        )

        heartbeat_interval = self._config.get("agent", {}).get("heartbeat_interval_minutes")
        heartbeat_task: asyncio.Task[None] | None = None
        if heartbeat_interval and heartbeat_interval > 0:
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(runtime, heartbeat_interval, tenant_id, shutdown_event)
            )

        logger.info(
            "OwlClaw '%s' is running (runtime=%s, cron=%d triggers, heartbeat=%s). "
            "Press Ctrl+C to stop.",
            self.name,
            "ready" if runtime.is_initialized else "not initialized",
            len(self.cron_registry.list_triggers()),
            f"{heartbeat_interval}min" if heartbeat_interval else "disabled",
        )

        try:
            await shutdown_event.wait()
        finally:
            logger.info("Shutting down OwlClaw '%s'...", self.name)
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            await self.stop()
            logger.info("OwlClaw '%s' stopped.", self.name)

    async def _heartbeat_loop(
        self,
        runtime: AgentRuntime,
        interval_minutes: int | float,
        tenant_id: str,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Periodic heartbeat that triggers Agent runs when events are pending."""
        interval_seconds = float(interval_minutes) * 60
        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                pass

            try:
                logger.info(
                    "Heartbeat tick app=%s tenant_id=%s trigger=heartbeat entering_decision_loop=true",
                    self.name,
                    tenant_id,
                )
                result = await runtime.trigger_event(
                    event_name="heartbeat",
                    payload={"source": "heartbeat"},
                    focus=None,
                    tenant_id=tenant_id,
                )
                logger.info(
                    "Heartbeat result app=%s status=%s reason=%s tool_calls_total=%s",
                    self.name,
                    result.get("status"),
                    result.get("reason"),
                    result.get("tool_calls_total", 0),
                )
            except Exception:
                logger.exception("Heartbeat trigger failed for '%s'", self.name)
