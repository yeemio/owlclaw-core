"""Binding schema models and validation helpers."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast

BINDING_TYPES = {"http", "queue", "sql", "grpc"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
QUEUE_PROVIDERS = {"kafka", "rabbitmq", "redis"}
QUEUE_FORMATS = {"json", "avro", "protobuf"}
ENV_VAR_REF = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")


@dataclass(slots=True)
class RetryConfig:
    max_attempts: int = 3
    backoff_ms: int = 1000
    backoff_multiplier: float = 2.0


@dataclass(slots=True)
class BindingConfig:
    type: Literal["http", "queue", "sql", "grpc"]
    mode: Literal["active", "shadow"] = "active"
    timeout_ms: int = 5000
    retry: RetryConfig = field(default_factory=RetryConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HTTPBindingConfig(BindingConfig):
    type: Literal["http"] = "http"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    body_template: dict[str, Any] | None = None
    response_mapping: dict[str, Any] = field(default_factory=dict)
    allowed_hosts: list[str] = field(default_factory=list)
    allow_private_network: bool = False


@dataclass(slots=True)
class QueueBindingConfig(BindingConfig):
    type: Literal["queue"] = "queue"
    provider: Literal["kafka", "rabbitmq", "redis"] = "kafka"
    connection: str = ""
    topic: str = ""
    format: Literal["json", "avro", "protobuf"] = "json"
    headers_mapping: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SQLBindingConfig(BindingConfig):
    type: Literal["sql"] = "sql"
    connection: str = ""
    query: str = ""
    read_only: bool = True
    parameter_mapping: dict[str, str] = field(default_factory=dict)
    max_rows: int = 1000


def parse_binding_config(data: dict[str, Any]) -> BindingConfig:
    """Parse typed binding config from plain dict."""
    validate_binding_config(data)
    retry_data = data.get("retry", {})
    retry = RetryConfig(
        max_attempts=int(retry_data.get("max_attempts", 3)),
        backoff_ms=int(retry_data.get("backoff_ms", 1000)),
        backoff_multiplier=float(retry_data.get("backoff_multiplier", 2.0)),
    )

    common: dict[str, Any] = {
        "mode": data.get("mode", "active"),
        "timeout_ms": int(data.get("timeout_ms", 5000)),
        "retry": retry,
    }
    binding_type = str(data["type"])
    if binding_type == "http":
        method_raw = str(data.get("method", "GET")).upper()
        method = method_raw if method_raw in HTTP_METHODS else "GET"
        return HTTPBindingConfig(
            method=cast(Literal["GET", "POST", "PUT", "PATCH", "DELETE"], method),
            url=str(data.get("url", "")),
            headers=_to_str_dict(data.get("headers", {})),
            body_template=_to_dict_or_none(data.get("body_template")),
            response_mapping=_to_dict(data.get("response_mapping", {})),
            allowed_hosts=_to_str_list(data.get("allowed_hosts", [])),
            allow_private_network=bool(data.get("allow_private_network", False)),
            **common,
        )
    if binding_type == "queue":
        provider_raw = str(data.get("provider", "kafka"))
        provider = provider_raw if provider_raw in QUEUE_PROVIDERS else "kafka"
        format_raw = str(data.get("format", "json"))
        format_value = format_raw if format_raw in QUEUE_FORMATS else "json"
        return QueueBindingConfig(
            provider=cast(Literal["kafka", "rabbitmq", "redis"], provider),
            connection=str(data.get("connection", "")),
            topic=str(data.get("topic", "")),
            format=cast(Literal["json", "avro", "protobuf"], format_value),
            headers_mapping=_to_str_dict(data.get("headers_mapping", {})),
            **common,
        )
    if binding_type == "sql":
        return SQLBindingConfig(
            connection=str(data.get("connection", "")),
            query=str(data.get("query", "")),
            read_only=bool(data.get("read_only", True)),
            parameter_mapping=_to_str_dict(data.get("parameter_mapping", {})),
            max_rows=int(data.get("max_rows", 1000)),
            **common,
        )
    return BindingConfig(type="grpc", **common)


def validate_binding_config(data: dict[str, Any]) -> None:
    """Validate binding config shape and security basics."""
    errors: list[str] = []
    binding_type = data.get("type")
    if binding_type not in BINDING_TYPES:
        errors.append(f"binding.type must be one of {sorted(BINDING_TYPES)}")

    mode = data.get("mode", "active")
    if mode not in {"active", "shadow"}:
        errors.append("binding.mode must be one of ['active', 'shadow']")

    timeout_ms = data.get("timeout_ms", 5000)
    if not isinstance(timeout_ms, int) or timeout_ms <= 0:
        errors.append("binding.timeout_ms must be int > 0")

    retry = data.get("retry", {})
    if retry and not isinstance(retry, dict):
        errors.append("binding.retry must be object")

    if binding_type == "http":
        _require_fields(data, ("method", "url"), errors, "http")
        method = str(data.get("method", "")).upper()
        if method and method not in HTTP_METHODS:
            errors.append(f"http.method must be one of {sorted(HTTP_METHODS)}")
        _validate_plaintext_secrets(_to_str_dict(data.get("headers", {})), errors, "http.headers")
        allowed_hosts = data.get("allowed_hosts", [])
        if not isinstance(allowed_hosts, list):
            errors.append("http.allowed_hosts must be a list of hostnames or IPs")
        if "allow_private_network" in data and not isinstance(data.get("allow_private_network"), bool):
            errors.append("http.allow_private_network must be boolean")
    elif binding_type == "queue":
        _require_fields(data, ("provider", "connection", "topic"), errors, "queue")
        provider = str(data.get("provider", ""))
        if provider and provider not in QUEUE_PROVIDERS:
            errors.append(f"queue.provider must be one of {sorted(QUEUE_PROVIDERS)}")
        fmt = str(data.get("format", "json"))
        if fmt and fmt not in QUEUE_FORMATS:
            errors.append(f"queue.format must be one of {sorted(QUEUE_FORMATS)}")
        if not _is_env_ref(str(data.get("connection", ""))):
            errors.append("queue.connection must use ${ENV_VAR} reference")
    elif binding_type == "sql":
        _require_fields(data, ("connection", "query"), errors, "sql")
        if not _is_env_ref(str(data.get("connection", ""))):
            errors.append("sql.connection must use ${ENV_VAR} reference")
        query = str(data.get("query", ""))
        if query and ":" not in query:
            errors.append("sql.query must use parameterized placeholders like ':param'")
        max_rows = data.get("max_rows", 1000)
        if not isinstance(max_rows, int) or max_rows <= 0:
            errors.append("sql.max_rows must be int > 0")
    elif binding_type == "grpc":
        errors.append("grpc binding is not yet implemented; use http, queue, or sql")

    if errors:
        raise ValueError("; ".join(errors))


def _require_fields(data: dict[str, Any], fields: tuple[str, ...], errors: list[str], prefix: str) -> None:
    for field_name in fields:
        value = data.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"{prefix}.{field_name} is required")


def _to_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _to_dict_or_none(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return value if isinstance(value, dict) else {}


def _to_str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _to_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized:
            out.append(normalized)
    return out


def _is_env_ref(value: str) -> bool:
    return bool(ENV_VAR_REF.match(value))


def _validate_plaintext_secrets(headers: dict[str, str], errors: list[str], prefix: str) -> None:
    sensitive_keys = {"authorization", "token", "x-api-key", "api-key", "secret", "password"}
    for key, value in headers.items():
        if key.lower() in sensitive_keys and not _is_env_ref(value):
            errors.append(f"{prefix}.{key} must use ${{ENV_VAR}} reference")
