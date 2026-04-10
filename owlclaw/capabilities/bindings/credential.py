"""Credential resolution utilities for declarative bindings."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CredentialResolver:
    """Resolve ${ENV_VAR} references from multiple secret sources."""

    def __init__(self, env_file: Path | None = None, config_secrets: dict[str, str] | None = None) -> None:
        self._env_file = Path(env_file) if env_file else None
        self._config_secrets = config_secrets or {}
        self._env_file_values = self._load_env_file(self._env_file) if self._env_file else {}

    def resolve(self, value: str) -> str:
        """Resolve variable placeholders in a string value."""
        if not isinstance(value, str):
            return str(value)

        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            if var_name in os.environ:
                return os.environ[var_name]
            if var_name in self._env_file_values:
                return self._env_file_values[var_name]
            if var_name in self._config_secrets:
                return self._config_secrets[var_name]
            raise ValueError(
                f"Missing credential reference: {var_name}. "
                "Set in environment, .env, or owlclaw.yaml secrets."
            )

        return ENV_VAR_PATTERN.sub(_replace, value)

    def resolve_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively resolve placeholders in nested dict/list values."""
        resolved: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, str):
                resolved[key] = self.resolve(value)
            elif isinstance(value, dict):
                resolved[key] = self.resolve_dict(value)
            elif isinstance(value, list):
                resolved[key] = [self.resolve(item) if isinstance(item, str) else item for item in value]
            else:
                resolved[key] = value
        return resolved

    @staticmethod
    def contains_potential_secret(value: str) -> bool:
        """Heuristic plaintext secret detection."""
        if not value or not isinstance(value, str):
            return False
        patterns = [
            r"(?i)\b(sk-|pk-|ghp_|gho_|glpat-)[A-Za-z0-9]{10,}",
            r"(?i)\b(token|secret|password|api[_-]?key)\b.{0,4}[=:].{8,}",
            r"(?i)^bearer\s+[A-Za-z0-9\-_.=]{12,}$",
        ]
        return any(re.search(pattern, value) for pattern in patterns)

    @staticmethod
    def _load_env_file(path: Path | None) -> dict[str, str]:
        if path is None or not path.exists():
            return {}
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw_value = stripped.split("=", 1)
            values[key.strip()] = raw_value.strip().strip("'\"")
        return values

