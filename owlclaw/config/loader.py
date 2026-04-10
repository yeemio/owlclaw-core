"""YAML configuration loader utilities."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


class ConfigLoadError(ValueError):
    """Raised when configuration YAML cannot be parsed."""


class YAMLConfigLoader:
    """Load owlclaw.yaml with deterministic path resolution."""

    DEFAULT_FILENAME = "owlclaw.yaml"

    @classmethod
    def resolve_path(cls, cli_path: str | None = None) -> Path:
        """Resolve config path by priority: env -> cli -> cwd default."""
        env_path = os.environ.get("OWLCLAW_CONFIG", "").strip()
        if env_path:
            return Path(env_path)
        if cli_path and cli_path.strip():
            return Path(cli_path.strip())
        return Path.cwd() / cls.DEFAULT_FILENAME

    @classmethod
    def load_dict(cls, path: str | Path | None = None) -> dict[str, Any]:
        """Load YAML into dict. Missing or empty file yields empty dict."""
        target = Path(path) if path is not None else cls.resolve_path()
        if not target.exists():
            return {}
        text = target.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            if mark is not None:
                raise ConfigLoadError(
                    f"Invalid YAML at {target}:{mark.line + 1}:{mark.column + 1}"
                ) from exc
            raise ConfigLoadError(f"Invalid YAML at {target}") from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ConfigLoadError(f"Config root must be mapping: {target}")
        return data

