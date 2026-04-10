"""Webhook trigger data models used by validation, transform, and execution layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

AuthMethodType = Literal["bearer", "hmac", "basic"]
ExecutionMode = Literal["sync", "async"]
ExecutionStatus = Literal["accepted", "running", "completed", "failed"]
EventType = Literal["request", "validation", "transformation", "execution"]


@dataclass(slots=True)
class RetryPolicy:
    """Retry policy for webhook-triggered execution."""

    max_attempts: int = 3
    initial_delay_ms: int = 1000
    max_delay_ms: int = 30000
    backoff_multiplier: float = 2.0


@dataclass(slots=True)
class AuthMethod:
    """Endpoint authentication strategy definition."""

    type: AuthMethodType
    token: str | None = None
    secret: str | None = None
    algorithm: Literal["sha256", "sha512"] | None = None
    username: str | None = None
    password: str | None = None


@dataclass(slots=True)
class EndpointConfig:
    """Webhook endpoint behavior configuration."""

    name: str
    target_agent_id: str
    auth_method: AuthMethod
    transformation_rule_id: str | None = None
    execution_mode: ExecutionMode = "async"
    timeout_seconds: float | None = None
    retry_policy: RetryPolicy | None = None
    enabled: bool = True


@dataclass(slots=True)
class EndpointFilter:
    """Filter options for listing webhook endpoints."""

    tenant_id: str = "default"
    target_agent_id: str | None = None
    enabled: bool | None = None


@dataclass(slots=True)
class WebhookEndpoint:
    """Registered webhook endpoint."""

    id: str
    url: str
    auth_token: str
    auth_token_hash: str
    config: EndpointConfig
    created_at: datetime
    updated_at: datetime
    tenant_id: str = "default"


@dataclass(slots=True)
class ValidationError:
    """Structured validation error for HTTP response mapping."""

    code: str
    message: str
    status_code: int | None = None
    details: dict[str, Any] | None = None


@dataclass(slots=True)
class ValidationResult:
    """Validation output with optional error details."""

    valid: bool
    error: ValidationError | None = None


@dataclass(slots=True)
class HttpRequest:
    """Normalized inbound HTTP request used by webhook validation layer."""

    headers: dict[str, str]
    body: str = ""


@dataclass(slots=True)
class ParsedPayload:
    """Parsed webhook payload and related request context."""

    content_type: str
    data: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    raw_body: str = ""


@dataclass(slots=True)
class FieldMapping:
    """Field mapping definition from payload to agent input."""

    source: str
    target: str
    transform: Literal["string", "number", "boolean", "date", "json"] | None = None
    default: Any = None


@dataclass(slots=True)
class TransformationRule:
    """Payload-to-agent transformation rule."""

    id: str
    name: str
    target_agent_id: str
    mappings: list[FieldMapping]
    target_schema: dict[str, Any] | None = None
    custom_logic: str | None = None


@dataclass(slots=True)
class AgentInput:
    """Execution payload passed to AgentRuntime."""

    agent_id: str
    parameters: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionOptions:
    """Execution options for trigger mode, timeout and idempotency."""

    mode: ExecutionMode = "async"
    timeout_seconds: float | None = None
    idempotency_key: str | None = None
    retry_policy: RetryPolicy | None = None


@dataclass(slots=True)
class ExecutionResult:
    """Execution status returned by execution layer."""

    execution_id: str
    status: ExecutionStatus
    started_at: datetime
    completed_at: datetime | None = None
    output: Any = None
    error: dict[str, Any] | None = None


@dataclass(slots=True)
class GovernanceContext:
    """Execution context passed to governance checks."""

    tenant_id: str
    endpoint_id: str
    agent_id: str
    request_id: str
    source_ip: str | None = None
    user_agent: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class GovernanceDecision:
    """Governance evaluation outcome."""

    allowed: bool
    status_code: int = 200
    reason: str | None = None
    policy_limits: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventFilter:
    """Filter options for querying webhook events."""

    tenant_id: str = "default"
    endpoint_id: str | None = None
    request_id: str | None = None
    event_type: EventType | None = None
    status: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    page: int = 1
    page_size: int = 50


@dataclass(slots=True)
class WebhookEventRecord:
    """Normalized webhook event record used by EventLogger."""

    id: str
    endpoint_id: str
    event_type: EventType
    timestamp: datetime
    request_id: str
    source_ip: str | None = None
    user_agent: str | None = None
    duration_ms: int | None = None
    status: str | None = None
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    tenant_id: str = "default"


@dataclass(slots=True)
class MetricRecord:
    """Single monitoring sample."""

    name: str
    value: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class MetricStats:
    """Aggregated monitoring metrics."""

    request_count: int
    success_rate: float
    failure_rate: float
    avg_response_time: float
    p95_response_time: float
    p99_response_time: float


@dataclass(slots=True)
class HealthCheckResult:
    """One health check result."""

    name: str
    status: Literal["pass", "fail"]
    message: str | None = None


@dataclass(slots=True)
class HealthStatusSnapshot:
    """Health summary of webhook service dependencies."""

    status: Literal["healthy", "degraded", "unhealthy"]
    checks: list[HealthCheckResult]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class AlertRecord:
    """Monitoring alert payload."""

    name: str
    severity: Literal["warning", "critical"]
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookGlobalConfig:
    """Global webhook runtime settings."""

    timeout_seconds: float = 30.0
    max_retries: int = 3
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@dataclass(slots=True)
class WebhookSystemConfig:
    """Webhook system config including endpoint-level overrides."""

    global_config: WebhookGlobalConfig = field(default_factory=WebhookGlobalConfig)
    endpoints: dict[str, dict[str, Any]] = field(default_factory=dict)
