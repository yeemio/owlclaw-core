"""FastAPI gateway for webhook trigger pipeline."""

from __future__ import annotations

import hmac
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response

from owlclaw.triggers.webhook.event_logger import EventLogger, build_event
from owlclaw.triggers.webhook.execution import ExecutionTrigger
from owlclaw.triggers.webhook.governance import GovernanceClient
from owlclaw.triggers.webhook.manager import WebhookEndpointManager
from owlclaw.triggers.webhook.monitoring import MonitoringService
from owlclaw.triggers.webhook.transformer import PayloadTransformer
from owlclaw.triggers.webhook.types import (
    AuthMethod,
    AuthMethodType,
    EndpointConfig,
    EventFilter,
    ExecutionMode,
    ExecutionOptions,
    FieldMapping,
    GovernanceContext,
    HttpRequest,
    MetricRecord,
    RetryPolicy,
    TransformationRule,
    ValidationError,
)
from owlclaw.triggers.webhook.validator import RequestValidator


@dataclass(slots=True)
class HttpGatewayConfig:
    """Configuration for webhook HTTP gateway."""

    cors_origins: list[str] = field(default_factory=list)
    tls_enabled: bool = False
    per_ip_limit_per_minute: int = 120
    per_endpoint_limit_per_minute: int = 300
    admin_token: str | None = None
    max_content_length_bytes: int = 1_048_576


class _RateLimiter:
    def __init__(self, *, per_ip_limit: int, per_endpoint_limit: int) -> None:
        self._per_ip_limit = per_ip_limit
        self._per_endpoint_limit = per_endpoint_limit
        self._ip_window: dict[str, deque[datetime]] = {}
        self._endpoint_window: dict[str, deque[datetime]] = {}

    def check(self, ip: str, endpoint_id: str) -> ValidationError | None:
        now = datetime.now(timezone.utc)
        if self._check_key(self._ip_window, ip, now, self._per_ip_limit):
            return ValidationError(code="RATE_LIMITED", message="ip rate limit exceeded", status_code=429)
        if self._check_key(self._endpoint_window, endpoint_id, now, self._per_endpoint_limit):
            return ValidationError(code="RATE_LIMITED", message="endpoint rate limit exceeded", status_code=429)
        return None

    @staticmethod
    def _check_key(store: dict[str, deque[datetime]], key: str, now: datetime, limit: int) -> bool:
        window = store.get(key)
        if window is None:
            window = deque()
            store[key] = window
        cutoff = now - timedelta(minutes=1)
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= limit:
            return True
        window.append(now)
        return False


_SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "x-signature",
    "x-admin-token",
    "cookie",
    "set-cookie",
}


async def _read_body_with_limit(request: Request, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("request body too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _sanitize_logged_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        normalized = key.strip().lower()
        if normalized in _SENSITIVE_HEADERS:
            if normalized in {"authorization", "proxy-authorization"}:
                prefix = value.split(" ", 1)[0].strip()
                sanitized[key] = f"{prefix} ***" if prefix else "***"
            else:
                sanitized[key] = "***"
            continue
        sanitized[key] = value
    return sanitized


def create_webhook_app(
    *,
    manager: WebhookEndpointManager,
    validator: RequestValidator,
    transformer: PayloadTransformer,
    governance: GovernanceClient,
    execution: ExecutionTrigger,
    event_logger: EventLogger,
    monitoring: MonitoringService,
    config: HttpGatewayConfig | None = None,
) -> FastAPI:
    """Create FastAPI app bound to webhook trigger services."""

    cfg = config or HttpGatewayConfig()
    app = FastAPI(title="OwlClaw Webhook Gateway")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.tls_enabled = cfg.tls_enabled
    limiter = _RateLimiter(
        per_ip_limit=cfg.per_ip_limit_per_minute,
        per_endpoint_limit=cfg.per_endpoint_limit_per_minute,
    )

    @app.middleware("http")
    async def _request_trace_middleware(request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/webhooks/"):
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > cfg.max_content_length_bytes:
                        return cast(
                            Response,
                            _error_response(
                                ValidationError(
                                    code="REQUEST_TOO_LARGE",
                                    message="request body too large",
                                    status_code=413,
                                ),
                                request_id=request.headers.get("x-request-id", str(uuid4())),
                            ),
                        )
                except ValueError:
                    pass
        request_id = request.headers.get("x-request-id", str(uuid4()))
        request.state.request_id = request_id
        started = datetime.now(timezone.utc)
        response = cast(Response, await call_next(request))
        elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
        response.headers["x-request-id"] = request_id
        await monitoring.record_metric(MetricRecord(name="response_time_ms", value=elapsed))
        return response

    async def require_admin_token(request: Request) -> None:
        expected = cfg.admin_token
        if not expected:
            raise HTTPException(status_code=500, detail="admin token not configured")
        provided = request.headers.get("x-admin-token")
        if not provided:
            authorization = request.headers.get("authorization", "")
            prefix = "Bearer "
            if authorization.startswith(prefix):
                provided = authorization[len(prefix) :].strip()
        if provided is None:
            provided = ""
        if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(status_code=401, detail="admin token required")

    @app.post("/webhooks/{endpoint_id}")
    async def receive_webhook(endpoint_id: str, request: Request) -> JSONResponse:
        request_id = str(getattr(request.state, "request_id", uuid4()))
        ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent")
        try:
            raw_body_bytes = await _read_body_with_limit(request, cfg.max_content_length_bytes)
        except ValueError:
            await monitoring.record_metric(MetricRecord(name="request_status", value=1, tags={"status": "failure"}))
            return _error_response(
                ValidationError(
                    code="REQUEST_TOO_LARGE",
                    message="request body too large",
                    status_code=413,
                ),
                request_id=request_id,
            )
        try:
            raw_body = raw_body_bytes.decode("utf-8")
        except UnicodeDecodeError:
            await monitoring.record_metric(MetricRecord(name="request_status", value=1, tags={"status": "failure"}))
            return _error_response(
                ValidationError(
                    code="INVALID_ENCODING",
                    message="request body must be valid UTF-8",
                    status_code=400,
                ),
                request_id=request_id,
            )
        await monitoring.record_metric(MetricRecord(name="request_count", value=1))
        await event_logger.log_request(
            build_event(
                endpoint_id=endpoint_id,
                request_id=request_id,
                event_type="request",
                source_ip=ip,
                user_agent=user_agent,
                data={"headers": _sanitize_logged_headers(dict(request.headers))},
            )
        )
        limit_error = limiter.check(ip=ip, endpoint_id=endpoint_id)
        if limit_error is not None:
            await monitoring.record_metric(MetricRecord(name="request_status", value=1, tags={"status": "failure"}))
            return _error_response(limit_error, request_id=request_id)

        endpoint, validation = await validator.validate_request(
            endpoint_id, HttpRequest(headers=dict(request.headers), body=raw_body)
        )
        if not validation.valid:
            await event_logger.log_validation(
                build_event(
                    endpoint_id=endpoint_id,
                    request_id=request_id,
                    event_type="validation",
                    status="failed",
                    error=(None if validation.error is None else {"code": validation.error.code, "message": validation.error.message}),
                )
            )
            await monitoring.record_metric(MetricRecord(name="request_status", value=1, tags={"status": "failure"}))
            assert validation.error is not None
            return _error_response(validation.error, request_id=request_id)
        assert endpoint is not None

        parsed, parse_result = transformer.parse_safe(HttpRequest(headers=dict(request.headers), body=raw_body))
        if not parse_result.valid:
            await monitoring.record_metric(MetricRecord(name="request_status", value=1, tags={"status": "failure"}))
            assert parse_result.error is not None
            return _error_response(parse_result.error, request_id=request_id)
        assert parsed is not None
        await event_logger.log_transformation(
            build_event(
                endpoint_id=endpoint_id,
                request_id=request_id,
                event_type="transformation",
                status="completed",
                data={"content_type": parsed.content_type},
            )
        )

        rule = TransformationRule(
            id=str(uuid4()),
            name="default-rule",
            target_agent_id=endpoint.config.target_agent_id,
            mappings=[FieldMapping(source="$", target="payload", transform=None)],
        )
        agent_input = transformer.transform(parsed, rule)
        governance_result = await governance.validate_execution(
            GovernanceContext(
                tenant_id=endpoint.tenant_id,
                endpoint_id=endpoint.id,
                agent_id=endpoint.config.target_agent_id,
                request_id=request_id,
                source_ip=ip,
                user_agent=user_agent,
            )
        )
        if not governance_result.valid:
            await monitoring.record_metric(MetricRecord(name="request_status", value=1, tags={"status": "failure"}))
            assert governance_result.error is not None
            return _error_response(governance_result.error, request_id=request_id)

        result = await execution.trigger(
            agent_input,
            options=ExecutionOptions(
                mode=endpoint.config.execution_mode,
                timeout_seconds=endpoint.config.timeout_seconds,
                idempotency_key=request.headers.get("x-idempotency-key"),
                retry_policy=endpoint.config.retry_policy,
            ),
        )
        status = "success" if result.status in {"accepted", "running", "completed"} else "failure"
        await monitoring.record_metric(MetricRecord(name="request_status", value=1, tags={"status": status}))
        await event_logger.log_execution(
            build_event(
                endpoint_id=endpoint.id,
                request_id=request_id,
                event_type="execution",
                status=result.status,
                data={"execution_id": result.execution_id},
                error=result.error,
            )
        )
        return JSONResponse(
            status_code=202,
            content={
                "execution_id": result.execution_id,
                "status": result.status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    @app.post("/endpoints", dependencies=[Depends(require_admin_token)])
    async def create_endpoint(payload: dict[str, Any]) -> JSONResponse:
        config_payload = payload.get("config", payload)
        config = EndpointConfig(
            name=str(config_payload["name"]),
            target_agent_id=str(config_payload["target_agent_id"]),
            auth_method=AuthMethod(
                type=_normalize_auth_method_type(config_payload["auth_method"]["type"]),
                token=config_payload["auth_method"].get("token"),
                secret=config_payload["auth_method"].get("secret"),
                algorithm=config_payload["auth_method"].get("algorithm"),
                username=config_payload["auth_method"].get("username"),
                password=config_payload["auth_method"].get("password"),
            ),
            execution_mode=_normalize_execution_mode(config_payload.get("execution_mode", "async")),
            timeout_seconds=config_payload.get("timeout_seconds"),
            retry_policy=(
                None
                if config_payload.get("retry_policy") is None
                else RetryPolicy(
                    max_attempts=int(config_payload["retry_policy"].get("max_attempts", 3)),
                    initial_delay_ms=int(config_payload["retry_policy"].get("initial_delay_ms", 1000)),
                    max_delay_ms=int(config_payload["retry_policy"].get("max_delay_ms", 30000)),
                    backoff_multiplier=float(config_payload["retry_policy"].get("backoff_multiplier", 2.0)),
                )
            ),
        )
        endpoint = await manager.create_endpoint(config)
        return JSONResponse(status_code=201, content={"id": endpoint.id, "url": endpoint.url, "config": asdict(endpoint.config)})

    @app.get("/endpoints", dependencies=[Depends(require_admin_token)])
    async def list_endpoints() -> JSONResponse:
        endpoints = await manager.list_endpoints()
        return JSONResponse(
            status_code=200,
            content={"items": [{"id": item.id, "url": item.url, "config": asdict(item.config)} for item in endpoints]},
        )

    @app.get("/endpoints/{endpoint_id}", dependencies=[Depends(require_admin_token)])
    async def get_endpoint(endpoint_id: str) -> JSONResponse:
        endpoint = await manager.get_endpoint(endpoint_id)
        if endpoint is None:
            return _error_response(ValidationError(code="ENDPOINT_NOT_FOUND", message="endpoint not found", status_code=404), request_id=str(uuid4()))
        return JSONResponse(status_code=200, content={"id": endpoint.id, "url": endpoint.url, "config": asdict(endpoint.config)})

    @app.put("/endpoints/{endpoint_id}", dependencies=[Depends(require_admin_token)])
    async def update_endpoint(endpoint_id: str, payload: dict[str, Any]) -> JSONResponse:
        config_payload = payload.get("config", payload)
        config = EndpointConfig(
            name=str(config_payload["name"]),
            target_agent_id=str(config_payload["target_agent_id"]),
            auth_method=AuthMethod(
                type=_normalize_auth_method_type(config_payload["auth_method"]["type"]),
                token=config_payload["auth_method"].get("token"),
                secret=config_payload["auth_method"].get("secret"),
                algorithm=config_payload["auth_method"].get("algorithm"),
                username=config_payload["auth_method"].get("username"),
                password=config_payload["auth_method"].get("password"),
            ),
            execution_mode=_normalize_execution_mode(config_payload.get("execution_mode", "async")),
            timeout_seconds=config_payload.get("timeout_seconds"),
            retry_policy=(
                None
                if config_payload.get("retry_policy") is None
                else RetryPolicy(
                    max_attempts=int(config_payload["retry_policy"].get("max_attempts", 3)),
                    initial_delay_ms=int(config_payload["retry_policy"].get("initial_delay_ms", 1000)),
                    max_delay_ms=int(config_payload["retry_policy"].get("max_delay_ms", 30000)),
                    backoff_multiplier=float(config_payload["retry_policy"].get("backoff_multiplier", 2.0)),
                )
            ),
            enabled=bool(config_payload.get("enabled", True)),
        )
        updated = await manager.update_endpoint(endpoint_id, config)
        return JSONResponse(status_code=200, content={"id": updated.id, "url": updated.url, "config": asdict(updated.config)})

    @app.delete("/endpoints/{endpoint_id}", dependencies=[Depends(require_admin_token)])
    async def delete_endpoint(endpoint_id: str) -> JSONResponse:
        await manager.delete_endpoint(endpoint_id)
        return JSONResponse(status_code=204, content=None)

    @app.get("/health")
    async def health() -> JSONResponse:
        health_status = await monitoring.get_health_status()
        return JSONResponse(
            status_code=200,
            content={
                "status": health_status.status,
                "checks": [{"name": c.name, "status": c.status, "message": c.message} for c in health_status.checks],
                "timestamp": health_status.timestamp.isoformat(),
            },
        )

    @app.get("/metrics")
    async def metrics() -> JSONResponse:
        stats = await monitoring.get_metrics(window="realtime")
        return JSONResponse(
            status_code=200,
            content={
                "request_count": stats.request_count,
                "success_rate": stats.success_rate,
                "failure_rate": stats.failure_rate,
                "avg_response_time": stats.avg_response_time,
                "p95_response_time": stats.p95_response_time,
                "p99_response_time": stats.p99_response_time,
            },
        )

    @app.get("/events", dependencies=[Depends(require_admin_token)])
    async def events(request_id: str | None = None) -> JSONResponse:
        items = await event_logger.query_events(EventFilter(tenant_id="default", request_id=request_id))
        return JSONResponse(
            status_code=200,
            content={"items": [jsonable_encoder(asdict(item)) for item in items]},
        )

    return app


def _error_response(error: ValidationError, *, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code or 400,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        },
    )


def _normalize_auth_method_type(value: object) -> AuthMethodType:
    normalized = str(value)
    if normalized not in {"bearer", "hmac", "basic"}:
        raise ValueError("auth_method.type must be one of bearer/hmac/basic")
    return cast(AuthMethodType, normalized)


def _normalize_execution_mode(value: object) -> ExecutionMode:
    normalized = str(value)
    if normalized not in {"sync", "async"}:
        raise ValueError("execution_mode must be sync or async")
    return cast(ExecutionMode, normalized)
