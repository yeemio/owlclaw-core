"""Binding executor interface and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from owlclaw.capabilities.bindings.schema import BindingConfig


class BindingExecutor(ABC):
    """Interface for binding executors."""

    @abstractmethod
    async def execute(self, config: BindingConfig, parameters: dict[str, Any]) -> dict[str, Any]:
        """Execute binding call and return normalized response."""

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Validate type-specific config payload."""

    @property
    @abstractmethod
    def supported_modes(self) -> list[str]:
        """Supported execution modes."""


class BindingExecutorRegistry:
    """Type-based executor registry."""

    def __init__(self) -> None:
        self._executors: dict[str, BindingExecutor] = {}

    def register(self, binding_type: str, executor: BindingExecutor) -> None:
        self._executors[binding_type] = executor

    def get(self, binding_type: str) -> BindingExecutor:
        executor = self._executors.get(binding_type)
        if executor is None:
            supported = ", ".join(sorted(self._executors.keys())) or "<none>"
            raise ValueError(f"Unknown binding type '{binding_type}'. Available types: {supported}")
        return executor

    def list_types(self) -> list[str]:
        return sorted(self._executors.keys())

