"""Configuration manager for OwlClaw."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any, ClassVar

from owlclaw.config.loader import YAMLConfigLoader
from owlclaw.config.models import OwlClawConfig

ConfigListener = Callable[[OwlClawConfig, OwlClawConfig], None]


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in updates.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce_env_value(raw: str) -> Any:
    value = raw.strip()
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass
    if value.startswith("[") or value.startswith("{"):
        try:
            import json

            return json.loads(value)
        except Exception:
            return value
    return value


def _collect_env_overrides(prefix: str = "OWLCLAW_") -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key, raw_value in os.environ.items():
        if not key.startswith(prefix):
            continue
        suffix = key[len(prefix) :]
        if not suffix:
            continue
        path = [p.strip().lower() for p in suffix.split("__") if p.strip()]
        if not path:
            continue
        cursor = overrides
        for part in path[:-1]:
            existing = cursor.get(part)
            if not isinstance(existing, dict):
                existing = {}
                cursor[part] = existing
            cursor = existing
        cursor[path[-1]] = _coerce_env_value(raw_value)
    return overrides


def _dict_diff(old: dict[str, Any], new: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    changes: dict[str, Any] = {}
    keys = set(old.keys()) | set(new.keys())
    for key in keys:
        path = f"{prefix}.{key}" if prefix else key
        old_val = old.get(key)
        new_val = new.get(key)
        if isinstance(old_val, dict) and isinstance(new_val, dict):
            changes.update(_dict_diff(old_val, new_val, path))
            continue
        if old_val != new_val:
            changes[path] = new_val
    return changes


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = [p for p in path.split(".") if p]
    if not parts:
        return
    cursor = target
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = value


@dataclass(frozen=True)
class ReloadResult:
    """Result for configuration hot reload."""

    applied: dict[str, Any]
    skipped: dict[str, Any]


class ConfigManager:
    """Thread-safe singleton for typed configuration access."""

    _instance: ClassVar[ConfigManager | None] = None
    _class_lock: ClassVar[Lock] = Lock()
    _hot_reloadable_prefixes: ClassVar[tuple[str, ...]] = (
        "governance",
        "security",
        "triggers",
        "agent.heartbeat_interval_minutes",
    )

    def __init__(self) -> None:
        self._lock = Lock()
        self._config = OwlClawConfig()
        self._listeners: list[ConfigListener] = []
        self._config_path: str | None = None
        self._runtime_overrides: dict[str, Any] = {}

    @classmethod
    def instance(cls) -> ConfigManager:
        """Get singleton instance."""
        if cls._instance is not None:
            return cls._instance
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        """Reset singleton state for isolated unit tests."""
        with cls._class_lock:
            cls._instance = None

    @classmethod
    def load(
        cls,
        config_path: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> ConfigManager:
        """Load configuration with precedence: overrides > env > YAML > defaults."""
        manager = cls.instance()
        yaml_data = YAMLConfigLoader.load_dict(config_path)
        env_overrides = _collect_env_overrides()
        merged = _deep_merge(yaml_data, env_overrides)
        runtime_overrides = overrides or {}
        merged = _deep_merge(merged, runtime_overrides)
        new_config = OwlClawConfig.model_validate(merged)
        with manager._lock:
            old = manager._config
            manager._config = new_config
            manager._config_path = config_path
            manager._runtime_overrides = runtime_overrides
            listeners = list(manager._listeners)
        for callback in listeners:
            callback(old, new_config)
        return manager

    def get(self) -> OwlClawConfig:
        """Return current config snapshot."""
        with self._lock:
            return self._config

    def on_change(self, callback: ConfigListener) -> None:
        """Register change listener."""
        with self._lock:
            self._listeners.append(callback)

    def reload(self, config_path: str | None = None) -> ReloadResult:
        """Reload config and apply only hot-reloadable changes."""
        with self._lock:
            old_cfg = self._config
            current_path = self._config_path
            runtime_overrides = dict(self._runtime_overrides)
            listeners = list(self._listeners)

        target_path = config_path if config_path is not None else current_path
        yaml_data = YAMLConfigLoader.load_dict(target_path)
        env_overrides = _collect_env_overrides()
        merged = _deep_merge(yaml_data, env_overrides)
        merged = _deep_merge(merged, runtime_overrides)
        candidate = OwlClawConfig.model_validate(merged)

        old_dump = old_cfg.model_dump(mode="python")
        new_dump = candidate.model_dump(mode="python")
        changes = _dict_diff(old_dump, new_dump)

        applied: dict[str, Any] = {}
        skipped: dict[str, Any] = {}
        hot_patch: dict[str, Any] = {}
        for path, value in changes.items():
            if path.startswith(self._hot_reloadable_prefixes):
                applied[path] = value
                _set_path(hot_patch, path, value)
            else:
                skipped[path] = value

        if applied:
            next_dump = _deep_merge(old_dump, hot_patch)
            next_cfg = OwlClawConfig.model_validate(next_dump)
            with self._lock:
                self._config = next_cfg
                self._config_path = target_path
            for callback in listeners:
                callback(old_cfg, next_cfg)
        else:
            with self._lock:
                self._config_path = target_path
        return ReloadResult(applied=applied, skipped=skipped)
