"""Configuration models for LangChain integration."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


class TracingConfig(BaseModel):
    """Tracing configuration for LangChain integration."""

    enabled: bool = True
    langfuse_integration: bool = True


class PrivacyConfig(BaseModel):
    """Privacy masking configuration for audit/log outputs."""

    mask_inputs: bool = False
    mask_outputs: bool = False
    mask_patterns: list[str] = Field(default_factory=lambda: ["api_key", "password", "secret", "token"])


class LangChainConfig(BaseModel):
    """Main configuration for LangChain integration."""

    enabled: bool = True
    version_check: bool = True
    min_version: str = "0.1.0"
    max_version: str = "0.3.0"
    default_timeout_seconds: int = Field(default=30, gt=0)
    max_concurrent_executions: int = Field(default=10, gt=0)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> LangChainConfig:
        """Load langchain config from YAML file."""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        section = raw.get("langchain", raw)
        section = cls._replace_env_vars(section)
        config = cls.model_validate(section)
        config.validate_semantics()
        return config

    @staticmethod
    def _replace_env_vars(value: Any) -> Any:
        """Recursively replace ${ENV_VAR} placeholders with environment values."""
        if isinstance(value, dict):
            return {k: LangChainConfig._replace_env_vars(v) for k, v in value.items()}
        if isinstance(value, list):
            return [LangChainConfig._replace_env_vars(v) for v in value]
        if isinstance(value, str):
            return _ENV_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), value)
        return value

    def validate_semantics(self) -> None:
        """Validate semantic constraints not covered by field-level validation."""
        min_parts = self._parse_semver(self.min_version)
        max_parts = self._parse_semver(self.max_version)
        if min_parts >= max_parts:
            raise ValueError("langchain min_version must be lower than max_version")

    @staticmethod
    def _parse_semver(version: str) -> tuple[int, int, int]:
        """Parse x.y.z semantic version string to integer tuple."""
        parts = version.split(".")
        if len(parts) != 3:
            raise ValueError(f"Invalid semantic version: {version}")
        try:
            return int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError as exc:
            raise ValueError(f"Invalid semantic version: {version}") from exc
