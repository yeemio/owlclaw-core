"""Runtime configuration loading, validation, and reload utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "max_function_calls": 50,
    "llm_timeout_seconds": 60.0,
    "run_timeout_seconds": 300.0,
    "llm_retry_attempts": 1,
    "llm_fallback_models": [],
    "heartbeat": {"enabled": True},
    # Keys listed here are injected from skill owlclaw_config.env even without OWLCLAW_SKILL_ prefix.
    "skill_env_allowlist": [],
}


def validate_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate runtime config and return normalized copy."""
    if not isinstance(config, dict):
        raise ValueError("runtime config must be a dictionary")
    normalized = dict(config)

    int_positive_fields = ("max_function_calls", "llm_retry_attempts")
    for field in int_positive_fields:
        if field in normalized:
            value = normalized[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")

    float_positive_fields = ("llm_timeout_seconds", "run_timeout_seconds")
    for field in float_positive_fields:
        if field in normalized:
            value = normalized[field]
            if isinstance(value, bool) or not isinstance(value, int | float) or float(value) <= 0:
                raise ValueError(f"{field} must be a positive number")
            normalized[field] = float(value)

    if "model" in normalized:
        model = normalized["model"]
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        normalized["model"] = model.strip()

    if "llm_fallback_models" in normalized:
        fallback = normalized["llm_fallback_models"]
        if not isinstance(fallback, list):
            raise ValueError("llm_fallback_models must be a list")
        cleaned: list[str] = []
        for item in fallback:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("llm_fallback_models must contain non-empty strings")
            cleaned.append(item.strip())
        normalized["llm_fallback_models"] = cleaned

    if "heartbeat" in normalized:
        heartbeat = normalized["heartbeat"]
        if not isinstance(heartbeat, dict):
            raise ValueError("heartbeat must be a dictionary")
        enabled = heartbeat.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("heartbeat.enabled must be a boolean")

    if "skill_env_allowlist" in normalized:
        allowlist = normalized["skill_env_allowlist"]
        if not isinstance(allowlist, list):
            raise ValueError("skill_env_allowlist must be a list")
        cleaned: list[str] = []
        for item in allowlist:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("skill_env_allowlist must contain non-empty strings")
            cleaned.append(item.strip())
        normalized["skill_env_allowlist"] = cleaned

    return normalized


def merge_runtime_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            nested = dict(out[key])
            nested.update(value)
            out[key] = nested
        else:
            out[key] = value
    return out


def load_runtime_config(path: str | Path) -> dict[str, Any]:
    """Load runtime config from yaml file."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"runtime config file not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    runtime_config = data.get("runtime", data)
    if not isinstance(runtime_config, dict):
        raise ValueError("runtime config must be a mapping")
    merged = merge_runtime_config(DEFAULT_RUNTIME_CONFIG, runtime_config)
    return validate_runtime_config(merged)
