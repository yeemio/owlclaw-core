"""Configuration change listener helpers for runtime modules."""

from __future__ import annotations

from typing import Any

from owlclaw.agent.runtime.heartbeat import HeartbeatChecker
from owlclaw.config.manager import ConfigManager
from owlclaw.security.risk_gate import RiskGate


def register_governance_reload_listener(app: Any, manager: ConfigManager | None = None) -> None:
    """Register listener to refresh app governance config."""
    cfg_manager = manager or ConfigManager.instance()

    def _on_change(_old_cfg, new_cfg) -> None:  # type: ignore[no-untyped-def]
        governance_cfg = new_cfg.governance.model_dump(mode="python")
        if hasattr(app, "_governance_config"):
            app._governance_config = governance_cfg
        if hasattr(app, "_visibility_filter"):
            app._visibility_filter = None
        if hasattr(app, "_router"):
            app._router = None
        if hasattr(app, "_ensure_governance") and callable(app._ensure_governance):
            app._ensure_governance()

    cfg_manager.on_change(_on_change)


def register_security_reload_listener(visibility_filter: Any, manager: ConfigManager | None = None) -> None:
    """Register listener to refresh RiskGate settings in visibility filter."""
    cfg_manager = manager or ConfigManager.instance()

    def _on_change(_old_cfg, new_cfg) -> None:  # type: ignore[no-untyped-def]
        timeout = new_cfg.security.risk_gate.confirmation_timeout_seconds
        if hasattr(visibility_filter, "_risk_gate"):
            visibility_filter._risk_gate = RiskGate(confirmation_timeout_seconds=timeout)

    cfg_manager.on_change(_on_change)


def register_runtime_reload_listener(runtime: Any, manager: ConfigManager | None = None) -> None:
    """Register listener to hot-update runtime heartbeat settings."""
    cfg_manager = manager or ConfigManager.instance()

    def _on_change(_old_cfg, new_cfg) -> None:  # type: ignore[no-untyped-def]
        if not hasattr(runtime, "config") or not isinstance(runtime.config, dict):
            return
        runtime.config.setdefault("heartbeat", {})
        heartbeat_cfg = runtime.config["heartbeat"]
        if not isinstance(heartbeat_cfg, dict):
            heartbeat_cfg = {}
            runtime.config["heartbeat"] = heartbeat_cfg
        heartbeat_cfg["interval_minutes"] = new_cfg.agent.heartbeat_interval_minutes
        if hasattr(runtime, "agent_id"):
            runtime._heartbeat_checker = HeartbeatChecker(runtime.agent_id, heartbeat_cfg)

    cfg_manager.on_change(_on_change)

