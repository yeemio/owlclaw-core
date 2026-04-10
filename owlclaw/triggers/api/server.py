"""HTTP API trigger server implementation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from owlclaw.security.sanitizer import InputSanitizer
from owlclaw.triggers.api.auth import AuthProvider
from owlclaw.triggers.api.config import APITriggerConfig
from owlclaw.triggers.api.handler import (
    BodyTooLargeError,
    InvalidJSONPayloadError,
    parse_request_payload_with_limit,
)
from owlclaw.triggers.signal.api import register_signal_admin_route
from owlclaw.triggers.signal.router import SignalRouter

DEFAULT_RUNS_CACHE_MAXSIZE = 1000
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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
class GovernanceDecision:
    """Governance result for one incoming API request."""

    allowed: bool
    status_code: int | None = None
    reason: str | None = None


class GovernanceGateProtocol(Protocol):
    async def evaluate_request(
        self,
        event_name: str,
        tenant_id: str,
        payload: dict[str, Any],
    ) -> GovernanceDecision: ...


@dataclass(slots=True)
class _BucketState:
    tokens: float
    last_refill: float


LIMITER_STATES_MAXSIZE = 10_000


class _TokenBucketLimiter:
    """In-memory token bucket limiter keyed by arbitrary strings. States are bounded (LRU)."""

    def __init__(self, *, rate_per_minute: int | None, max_states: int = LIMITER_STATES_MAXSIZE) -> None:
        self._rate = int(rate_per_minute or 0)
        self._capacity = float(max(1, self._rate)) if self._rate > 0 else 0.0
        self._states: OrderedDict[str, _BucketState] = OrderedDict()
        self._max_states = max(1, int(max_states))
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._rate > 0

    async def allow(self, key: str) -> bool:
        if not self.enabled:
            return True
        now = monotonic()
        refill_rate = float(self._rate) / 60.0
        async with self._lock:
            state = self._states.get(key)
            if state is None:
                while len(self._states) >= self._max_states:
                    self._states.popitem(last=False)
                self._states[key] = _BucketState(tokens=self._capacity - 1.0, last_refill=now)
                return True
            self._states.move_to_end(key)
            elapsed = max(0.0, now - state.last_refill)
            state.tokens = min(self._capacity, state.tokens + elapsed * refill_rate)
            state.last_refill = now
            if state.tokens < 1.0:
                return False
            state.tokens -= 1.0
            return True


class APITriggerServer:
    """Starlette-based dynamic API trigger server."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8080,
        auth_provider: AuthProvider | None = None,
        agent_runtime: AgentRuntimeProtocol | None = None,
        governance_gate: GovernanceGateProtocol | None = None,
        sanitizer: InputSanitizer | None = None,
        ledger: LedgerProtocol | None = None,
        agent_id: str = "api-trigger",
        max_body_bytes: int = 1024 * 1024,
        cors_origins: list[str] | None = None,
        tenant_rate_limit_per_minute: int = 120,
        endpoint_rate_limit_per_minute: int = 60,
        runs_cache_maxsize: int = DEFAULT_RUNS_CACHE_MAXSIZE,
    ) -> None:
        self._host = host
        self._port = port
        self._auth_provider = auth_provider
        self._agent_runtime = agent_runtime
        self._governance_gate = governance_gate
        self._sanitizer = sanitizer if sanitizer is not None else InputSanitizer()
        self._ledger = ledger
        self._agent_id = agent_id
        self._max_body_bytes = max_body_bytes

        self._configs: dict[str, APITriggerConfig] = {}
        self._app = Starlette(routes=[])
        self._server: Any | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._runs_maxsize = max(1, int(runs_cache_maxsize))
        self._signal_admin_registered: bool = False
        self._tenant_limiter = _TokenBucketLimiter(rate_per_minute=tenant_rate_limit_per_minute)
        self._endpoint_limiter = _TokenBucketLimiter(rate_per_minute=endpoint_rate_limit_per_minute)
        origins = cors_origins if cors_origins is not None else []
        self._app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])
        self._app.router.routes.append(Route("/runs/{run_id}/result", endpoint=self._get_run_result, methods=["GET"]))

    @property
    def app(self) -> Starlette:
        return self._app

    @property
    def registered_endpoints_count(self) -> int:
        """Expose registered API trigger endpoint count without leaking config map."""
        return len(self._configs)

    def register(self, config: APITriggerConfig, fallback: Callable[[dict[str, Any]], Awaitable[Any]] | None = None) -> None:
        route_key = f"{config.method}:{config.path}"
        if route_key in self._configs:
            raise ValueError(f"API trigger already registered: {route_key}")

        async def endpoint(request: Request) -> JSONResponse:
            started = monotonic()
            auth_response, auth_identity = await self._authenticate(config, request)
            if auth_response is not None:
                await self._record_execution(
                    config=config,
                    run_id="auth-failed",
                    status="failed",
                    started=started,
                    payload={"auth_identity": auth_identity},
                    output={"error": "unauthorized"},
                    reason="auth_failed",
                )
                return auth_response

            try:
                parsed = await parse_request_payload_with_limit(request, self._max_body_bytes)
            except BodyTooLargeError:
                return JSONResponse({"error": "payload_too_large"}, status_code=413)
            except InvalidJSONPayloadError:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)
            body = parsed.body
            if self._sanitizer is not None:
                raw = json.dumps(body, ensure_ascii=False)
                sanitized = self._sanitizer.sanitize(raw, source="api")
                if sanitized.changed:
                    with contextlib.suppress(Exception):
                        parsed_body = json.loads(sanitized.sanitized)
                        body = parsed_body if isinstance(parsed_body, dict) else {"value": parsed_body}

            payload = {
                "body": body,
                "query": parsed.query,
                "path": parsed.path_params,
                "method": request.method,
                "url": self._safe_request_url(request),
                "auth_identity": auth_identity,
            }
            if not await self._allow_request(config):
                await self._record_execution(
                    config=config,
                    run_id="rate-limited",
                    status="blocked",
                    started=started,
                    payload=payload,
                    output=None,
                    reason="rate_limited",
                )
                return JSONResponse({"error": "rate_limited"}, status_code=429)

            if self._governance_gate is not None:
                decision = await self._governance_gate.evaluate_request(config.event_name, config.tenant_id, payload)
                if not decision.allowed:
                    status_code = decision.status_code if decision.status_code is not None else 429
                    await self._record_execution(
                        config=config,
                        run_id="governance-blocked",
                        status="blocked",
                        started=started,
                        payload=payload,
                        output=None,
                        reason=decision.reason or "governance_blocked",
                    )
                    return JSONResponse({"error": decision.reason or "governance_blocked"}, status_code=status_code)

            if config.response_mode == "sync":
                response = await self._handle_sync(config, payload, fallback, started)
                return response

            return await self._handle_async(config, payload, fallback, started, auth_identity)

        self._app.router.routes.append(Route(config.path, endpoint=endpoint, methods=[config.method]))
        self._configs[route_key] = config

    async def _allow_request(self, config: APITriggerConfig) -> bool:
        tenant_key = f"tenant:{config.tenant_id}"
        endpoint_key = f"endpoint:{config.tenant_id}:{config.method}:{config.path}"
        tenant_ok = await self._tenant_limiter.allow(tenant_key)
        if not tenant_ok:
            return False
        endpoint_ok = await self._endpoint_limiter.allow(endpoint_key)
        return endpoint_ok

    def register_signal_admin(
        self,
        *,
        signal_router: SignalRouter,
        path: str = "/admin/signal",
        require_auth: bool = True,
    ) -> None:
        """Register POST /admin/signal on the same Starlette service."""
        if self._signal_admin_registered:
            raise ValueError("Signal admin route already registered")
        register_signal_admin_route(
            app_routes=self._app.router.routes,
            router=signal_router,
            auth_provider=self._auth_provider,
            require_auth=require_auth,
            path=path,
        )
        self._signal_admin_registered = True

    async def start(self) -> None:
        if self._server_task is not None:
            return
        try:
            import uvicorn  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise RuntimeError("uvicorn is required to start APITriggerServer") from exc
        config = uvicorn.Config(self._app, host=self._host, port=self._port, log_level="warning")
        self._server = uvicorn.Server(config=config)
        self._server_task = asyncio.create_task(self._server.serve())

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._background_tasks:
            tasks = tuple(self._background_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._background_tasks.clear()
        if self._server_task is not None:
            await self._server_task
            self._server_task = None
            self._server = None

    async def _authenticate(self, config: APITriggerConfig, request: Request) -> tuple[JSONResponse | None, str | None]:
        if not config.auth_required:
            return None, None
        if self._auth_provider is None:
            return JSONResponse({"error": "unauthorized", "reason": "auth_provider_missing"}, status_code=401), None
        result = await self._auth_provider.authenticate(request)
        if result.ok:
            return None, result.identity
        return JSONResponse({"error": "unauthorized", "reason": result.reason or "auth_failed"}, status_code=401), result.identity

    async def _handle_sync(
        self,
        config: APITriggerConfig,
        payload: dict[str, Any],
        fallback: Callable[[dict[str, Any]], Awaitable[Any]] | None,
        started: float,
    ) -> JSONResponse:
        run_id = f"sync-{uuid4().hex}"
        if self._agent_runtime is None:
            if fallback is None:
                return JSONResponse({"error": "runtime_unavailable"}, status_code=503)
            result = await fallback(payload)
            await self._record_execution(config, run_id, "success", started, payload, {"result": result}, "fallback")
            return JSONResponse({"status": "ok", "result": result})

        try:
            result = await asyncio.wait_for(
                self._agent_runtime.trigger_event(
                    event_name=config.event_name,
                    payload=payload,
                    focus=config.focus,
                    tenant_id=config.tenant_id,
                ),
                timeout=float(config.sync_timeout_seconds),
            )
        except asyncio.TimeoutError:
            await self._record_execution(config, run_id, "failed", started, payload, None, "timeout", "sync timeout")
            return JSONResponse({"error": "timeout"}, status_code=408)

        await self._record_execution(config, run_id, "success", started, payload, {"result": result}, "sync_completed")
        return JSONResponse({"status": "ok", "result": result})

    async def _handle_async(
        self,
        config: APITriggerConfig,
        payload: dict[str, Any],
        fallback: Callable[[dict[str, Any]], Awaitable[Any]] | None,
        started: float,
        auth_identity: str | None,
    ) -> JSONResponse:
        run_id = f"run-{uuid4().hex}"
        while len(self._runs) >= self._runs_maxsize:
            self._runs.popitem(last=False)
        self._runs[run_id] = {
            "status": "pending",
            "_auth_required": bool(config.auth_required),
            "_auth_identity": auth_identity,
            "_tenant_id": config.tenant_id,
            "_query_count": 0,
        }

        async def _background() -> None:
            try:
                if self._agent_runtime is not None:
                    result = await self._agent_runtime.trigger_event(
                        event_name=config.event_name,
                        payload=payload,
                        focus=config.focus,
                        tenant_id=config.tenant_id,
                    )
                elif fallback is not None:
                    result = await fallback(payload)
                else:
                    raise RuntimeError("runtime_unavailable")
                self._runs[run_id] = {**self._runs.get(run_id, {}), "status": "completed", "result": result}
                await self._record_execution(config, run_id, "success", started, payload, {"result": result}, "async_completed")
            except asyncio.CancelledError:
                self._runs[run_id] = {**self._runs.get(run_id, {}), "status": "cancelled", "error": "Execution cancelled."}
                raise
            except Exception:
                self._runs[run_id] = {**self._runs.get(run_id, {}), "status": "failed", "error": "Execution failed."}
                await self._record_execution(
                    config, run_id, "failed", started, payload, None, "async_failed", "Execution failed."
                )

        task = asyncio.create_task(_background())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return JSONResponse(
            {"status": "accepted", "run_id": run_id},
            status_code=202,
            headers={"Location": f"/runs/{run_id}/result"},
        )

    async def _get_run_result(self, request: Request) -> JSONResponse:
        run_id = str(request.path_params.get("run_id", "")).strip()
        if not self._is_valid_run_id(run_id):
            return JSONResponse({"error": "invalid_run_id"}, status_code=400)
        run = self._runs.get(run_id)
        if run is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        query_identity: str | None = None
        auth_required = bool(run.get("_auth_required", False))
        if auth_required:
            if self._auth_provider is None:
                return JSONResponse({"error": "unauthorized", "reason": "auth_provider_missing"}, status_code=401)
            result = await self._auth_provider.authenticate(request)
            if not result.ok:
                return JSONResponse({"error": "unauthorized", "reason": result.reason or "auth_failed"}, status_code=401)
            expected_identity = run.get("_auth_identity")
            if expected_identity is not None and result.identity != expected_identity:
                return JSONResponse({"error": "not_found"}, status_code=404)
            query_identity = result.identity
        else:
            query_identity = "anonymous"
        query_tenant = self._resolve_query_tenant(request, run)
        run["_query_count"] = int(run.get("_query_count", 0)) + 1
        run["_last_query_identity"] = query_identity
        run["_last_query_tenant"] = query_tenant
        self._runs.move_to_end(run_id)
        public_run = {key: value for key, value in run.items() if not key.startswith("_")}
        return JSONResponse(
            {
                "run_id": run_id,
                **public_run,
                "query_audit": {
                    "query_identity": query_identity,
                    "query_tenant": query_tenant,
                    "query_count": run["_query_count"],
                },
            }
        )

    @staticmethod
    def _resolve_query_tenant(request: Request, run: dict[str, Any]) -> str:
        """Resolve audit tenant from request header with safe fallback."""
        header_tenant = request.headers.get("x-owlclaw-tenant", "").strip()
        if header_tenant:
            return header_tenant
        run_tenant = run.get("_tenant_id", "default")
        return str(run_tenant).strip() or "default"

    @staticmethod
    def _safe_request_url(request: Request) -> str:
        """Return path-only URL for audit payloads to avoid leaking query secrets."""
        parts = urlsplit(str(request.url))
        path = parts.path.strip() or "/"
        return path

    @staticmethod
    def _is_valid_run_id(run_id: str) -> bool:
        """Validate run id from URL path before using it in caches or logs."""
        return bool(_RUN_ID_PATTERN.fullmatch(run_id))

    async def _record_execution(
        self,
        config: APITriggerConfig,
        run_id: str,
        status: str,
        started: float,
        payload: dict[str, Any],
        output: dict[str, Any] | None,
        reason: str,
        error_message: str | None = None,
    ) -> None:
        if self._ledger is None:
            return
        with contextlib.suppress(Exception):
            await self._ledger.record_execution(
                tenant_id=config.tenant_id,
                agent_id=self._agent_id,
                run_id=run_id,
                capability_name="api_trigger",
                task_type="trigger",
                input_params=payload,
                output_result=output,
                decision_reasoning=reason,
                execution_time_ms=int((monotonic() - started) * 1000),
                llm_model="",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status=status,
                error_message=error_message,
            )
