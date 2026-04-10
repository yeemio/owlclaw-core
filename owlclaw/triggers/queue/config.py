"""Queue trigger configuration and validation helpers."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml  # type: ignore[import-untyped]

from owlclaw.triggers.queue.security import redact_sensitive_data

AckPolicy = Literal["ack", "nack", "requeue", "dlq"]
ParserType = Literal["json", "text", "binary"]
VALID_ACK_POLICIES: set[str] = {"ack", "nack", "requeue", "dlq"}
VALID_PARSER_TYPES: set[str] = {"json", "text", "binary"}


@dataclass(slots=True)
class QueueTriggerConfig:
    """Queue trigger runtime configuration."""

    queue_name: str
    consumer_group: str
    concurrency: int = 1
    ack_policy: AckPolicy = "ack"
    max_retries: int = 3
    retry_backoff_base: float = 1.0
    retry_backoff_multiplier: float = 2.0
    idempotency_window: int = 3600
    enable_dedup: bool = True
    parser_type: ParserType = "json"
    event_name_header: str = "x-event-name"
    default_tenant_id: str = "default"
    trust_tenant_header: bool = False
    tenant_header_name: str = "x-tenant-id"
    trusted_producer_header: str = "x-producer-id"
    trusted_producers: list[str] | None = None
    tenant_signature_header: str = "x-tenant-signature"
    tenant_signature_secret_env: str | None = None
    tenant_signature_secret_envs: list[str] | None = None
    governance_fail_open: bool = False
    focus: str | None = None
    adapter_config: dict[str, Any] | None = None

    def __repr__(self) -> str:
        """Return safe representation with sensitive fields redacted."""
        safe_adapter = redact_sensitive_data(self.adapter_config) if self.adapter_config is not None else None
        return (
            "QueueTriggerConfig("
            f"queue_name={self.queue_name!r}, "
            f"consumer_group={self.consumer_group!r}, "
            f"concurrency={self.concurrency!r}, "
            f"ack_policy={self.ack_policy!r}, "
            f"max_retries={self.max_retries!r}, "
            f"retry_backoff_base={self.retry_backoff_base!r}, "
            f"retry_backoff_multiplier={self.retry_backoff_multiplier!r}, "
            f"idempotency_window={self.idempotency_window!r}, "
            f"enable_dedup={self.enable_dedup!r}, "
            f"parser_type={self.parser_type!r}, "
            f"event_name_header={self.event_name_header!r}, "
            f"default_tenant_id={self.default_tenant_id!r}, "
            f"trust_tenant_header={self.trust_tenant_header!r}, "
            f"tenant_header_name={self.tenant_header_name!r}, "
            f"trusted_producer_header={self.trusted_producer_header!r}, "
            f"trusted_producers={self.trusted_producers!r}, "
            f"tenant_signature_header={self.tenant_signature_header!r}, "
            f"tenant_signature_secret_env={self.tenant_signature_secret_env!r}, "
            f"tenant_signature_secret_envs={self.tenant_signature_secret_envs!r}, "
            f"governance_fail_open={self.governance_fail_open!r}, "
            f"focus={self.focus!r}, "
            f"adapter_config={safe_adapter!r}"
            ")"
        )


def validate_config(config: QueueTriggerConfig) -> list[str]:
    """Validate queue trigger configuration and return error messages."""
    errors: list[str] = []

    if not config.queue_name.strip():
        errors.append("queue_name is required")
    if not config.consumer_group.strip():
        errors.append("consumer_group is required")
    if config.concurrency <= 0:
        errors.append("concurrency must be positive")
    if config.max_retries < 0:
        errors.append("max_retries must be non-negative")
    if config.retry_backoff_base <= 0:
        errors.append("retry_backoff_base must be positive")
    if config.retry_backoff_multiplier < 1.0:
        errors.append("retry_backoff_multiplier must be >= 1")
    if config.idempotency_window <= 0:
        errors.append("idempotency_window must be positive")
    if config.ack_policy not in VALID_ACK_POLICIES:
        errors.append(f"ack_policy must be one of {sorted(VALID_ACK_POLICIES)}")
    if config.parser_type not in VALID_PARSER_TYPES:
        errors.append(f"parser_type must be one of {sorted(VALID_PARSER_TYPES)}")
    if not config.default_tenant_id.strip():
        errors.append("default_tenant_id is required")
    if not config.tenant_header_name.strip():
        errors.append("tenant_header_name is required")
    if not config.trusted_producer_header.strip():
        errors.append("trusted_producer_header is required")
    if not config.tenant_signature_header.strip():
        errors.append("tenant_signature_header is required")
    if config.trusted_producers is not None and any(not producer.strip() for producer in config.trusted_producers):
        errors.append("trusted_producers entries must be non-empty strings")
    if config.tenant_signature_secret_env is not None and not config.tenant_signature_secret_env.strip():
        errors.append("tenant_signature_secret_env must be non-empty when provided")
    if config.tenant_signature_secret_envs is not None and any(not item.strip() for item in config.tenant_signature_secret_envs):
        errors.append("tenant_signature_secret_envs entries must be non-empty strings")

    return errors


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _replace_env_vars(value: Any) -> Any:
    """Recursively replace ${ENV_VAR} placeholders using process env."""
    if isinstance(value, dict):
        return {k: _replace_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_env_vars(v) for v in value]
    if not isinstance(value, str):
        return value

    def _lookup(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, "")

    return _ENV_PATTERN.sub(_lookup, value)


def _require_str(config_map: dict[str, Any], key: str, default: str) -> str:
    value = config_map.get(key, default)
    if value is None:
        return default
    return str(value)


def _require_int(config_map: dict[str, Any], key: str, default: int) -> int:
    value = config_map.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _require_float(config_map: dict[str, Any], key: str, default: float) -> float:
    value = config_map.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number") from exc


def _coerce_bool(config_map: dict[str, Any], key: str, default: bool) -> bool:
    value = config_map.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{key} must be a boolean")


def _coerce_str_list(config_map: dict[str, Any], key: str, default: list[str] | None = None) -> list[str] | None:
    value = config_map.get(key, default)
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"{key} must be a list of strings")
            stripped = item.strip()
            if stripped:
                normalized.append(stripped)
        return normalized
    raise ValueError(f"{key} must be a list of strings or comma-separated string")


def load_queue_trigger_config(config_path: str) -> QueueTriggerConfig:
    """Load queue trigger config from YAML file and validate it."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Queue trigger config root must be a mapping")

    config_map = raw.get("queue_trigger", raw)
    if not isinstance(config_map, dict):
        raise ValueError("queue_trigger section must be a mapping")
    config_map = _replace_env_vars(config_map)
    if not isinstance(config_map, dict):
        raise ValueError("queue_trigger section must be a mapping")
    config_dict = cast(dict[str, Any], config_map)
    adapter_config = config_dict.get("adapter")
    if adapter_config is not None and not isinstance(adapter_config, dict):
        raise ValueError("adapter section must be a mapping")

    config = QueueTriggerConfig(
        queue_name=_require_str(config_dict, "queue_name", ""),
        consumer_group=_require_str(config_dict, "consumer_group", ""),
        concurrency=_require_int(config_dict, "concurrency", 1),
        ack_policy=cast(AckPolicy, _require_str(config_dict, "ack_policy", "ack")),
        max_retries=_require_int(config_dict, "max_retries", 3),
        retry_backoff_base=_require_float(config_dict, "retry_backoff_base", 1.0),
        retry_backoff_multiplier=_require_float(config_dict, "retry_backoff_multiplier", 2.0),
        idempotency_window=_require_int(config_dict, "idempotency_window", 3600),
        enable_dedup=_coerce_bool(config_dict, "enable_dedup", True),
        parser_type=cast(ParserType, _require_str(config_dict, "parser_type", "json")),
        event_name_header=_require_str(config_dict, "event_name_header", "x-event-name"),
        default_tenant_id=_require_str(config_dict, "default_tenant_id", "default"),
        trust_tenant_header=_coerce_bool(config_dict, "trust_tenant_header", False),
        tenant_header_name=_require_str(config_dict, "tenant_header_name", "x-tenant-id"),
        trusted_producer_header=_require_str(config_dict, "trusted_producer_header", "x-producer-id"),
        trusted_producers=_coerce_str_list(config_dict, "trusted_producers", None),
        tenant_signature_header=_require_str(config_dict, "tenant_signature_header", "x-tenant-signature"),
        tenant_signature_secret_env=(
            None
            if config_dict.get("tenant_signature_secret_env") is None
            else _require_str(config_dict, "tenant_signature_secret_env", "")
        ),
        tenant_signature_secret_envs=_coerce_str_list(config_dict, "tenant_signature_secret_envs", None),
        governance_fail_open=_coerce_bool(config_dict, "governance_fail_open", False),
        focus=(None if config_dict.get("focus") is None else str(config_dict.get("focus"))),
        adapter_config=cast(dict[str, Any] | None, adapter_config),
    )

    errors = validate_config(config)
    if errors:
        raise ValueError(f"Invalid queue trigger config: {'; '.join(errors)}")
    return config
