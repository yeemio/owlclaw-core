"""Webhook configuration management with validation, hot-reload, and version history."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Literal, cast

import yaml  # type: ignore[import-untyped]

from owlclaw.triggers.webhook.types import WebhookGlobalConfig, WebhookSystemConfig


class WebhookConfigManager:
    """Manage webhook config lifecycle with validation and rollback."""

    def __init__(self) -> None:
        self._current = WebhookSystemConfig()
        self._versions: dict[str, WebhookSystemConfig] = {}
        self._version_order: list[str] = []
        self._listeners: list[Callable[[WebhookSystemConfig], None]] = []
        self._version = 0

    def load_from_file(self, path: str) -> WebhookSystemConfig:
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        data = yaml.safe_load(content) if path.endswith((".yml", ".yaml")) else json.loads(content)
        return self._load_from_mapping(data if isinstance(data, dict) else {})

    def load_from_mapping(self, data: dict[str, Any]) -> WebhookSystemConfig:
        return self._load_from_mapping(data)

    def apply_env_overrides(self, config: WebhookSystemConfig) -> WebhookSystemConfig:
        timeout_env = os.getenv("OWLCLAW_WEBHOOK_TIMEOUT_SECONDS")
        retries_env = os.getenv("OWLCLAW_WEBHOOK_MAX_RETRIES")
        log_level_env = os.getenv("OWLCLAW_WEBHOOK_LOG_LEVEL")
        updated = copy.deepcopy(config)
        if timeout_env is not None:
            updated.global_config.timeout_seconds = float(timeout_env)
        if retries_env is not None:
            updated.global_config.max_retries = int(retries_env)
        if log_level_env is not None:
            updated.global_config.log_level = _normalize_log_level(log_level_env)
        self.validate(updated)
        return updated

    def validate(self, config: WebhookSystemConfig) -> None:
        if config.global_config.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if config.global_config.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if config.global_config.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("log_level must be one of DEBUG/INFO/WARNING/ERROR")
        if not isinstance(config.endpoints, dict):
            raise ValueError("endpoints must be a dictionary")

    def update(self, config: WebhookSystemConfig) -> str:
        self.validate(config)
        self._version += 1
        version_id = f"v{self._version}"
        self._current = copy.deepcopy(config)
        self._versions[version_id] = copy.deepcopy(self._current)
        self._version_order.append(version_id)
        self._notify_listeners(self._current)
        return version_id

    def get_current(self) -> WebhookSystemConfig:
        return copy.deepcopy(self._current)

    def rollback(self, version_id: str) -> WebhookSystemConfig:
        target = self._versions.get(version_id)
        if target is None:
            raise KeyError(f"version not found: {version_id}")
        self._current = copy.deepcopy(target)
        if version_id in self._version_order:
            keep = self._version_order.index(version_id) + 1
            removed = self._version_order[keep:]
            self._version_order = self._version_order[:keep]
            for key in removed:
                self._versions.pop(key, None)
        self._notify_listeners(self._current)
        return self.get_current()

    def list_versions(self) -> list[str]:
        return list(self._version_order)

    def watch(self, callback: Callable[[WebhookSystemConfig], None]) -> None:
        self._listeners.append(callback)

    def _notify_listeners(self, config: WebhookSystemConfig) -> None:
        for listener in self._listeners:
            listener(copy.deepcopy(config))

    def _load_from_mapping(self, data: dict[str, Any]) -> WebhookSystemConfig:
        global_data = data.get("global", {})
        endpoints = data.get("endpoints", {})
        if global_data is None:
            global_data = {}
        if not isinstance(global_data, dict):
            raise ValueError("global must be a mapping")
        if not isinstance(endpoints, dict):
            raise ValueError("endpoints must be a mapping")
        config = WebhookSystemConfig(
            global_config=WebhookGlobalConfig(
                timeout_seconds=float(global_data.get("timeout_seconds", 30.0)),
                max_retries=int(global_data.get("max_retries", 3)),
                log_level=_normalize_log_level(global_data.get("log_level", "INFO")),
            ),
            endpoints=copy.deepcopy(endpoints),
        )
        self.validate(config)
        return config


def dump_config(config: WebhookSystemConfig) -> dict[str, Any]:
    """Serialize webhook config for persistence."""

    payload = asdict(config)
    payload["meta"] = {"dumped_at": datetime.now(timezone.utc).isoformat()}
    return payload


def _normalize_log_level(value: object) -> Literal["DEBUG", "INFO", "WARNING", "ERROR"]:
    normalized = str(value).upper()
    if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("log_level must be one of DEBUG/INFO/WARNING/ERROR")
    return cast(Literal["DEBUG", "INFO", "WARNING", "ERROR"], normalized)
