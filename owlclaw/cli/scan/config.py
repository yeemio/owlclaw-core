"""Configuration management for cli-scan."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from owlclaw.cli.scan.scanner import ScanConfig


class ConfigManager:
    """Load and validate cli-scan configuration files."""

    DEFAULT_FILE = ".owlclaw-scan.yaml"

    def load(self, project_path: Path, config_file: Path | None = None) -> ScanConfig:
        path = config_file or (project_path / self.DEFAULT_FILE)
        if not path.exists():
            return ScanConfig(project_path=project_path)
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError("config file must contain a mapping")
        data = self.validate(payload)
        return self.from_dict(project_path, data)

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        validated = {
            "include_patterns": payload.get("include_patterns", ["*.py"]),
            "exclude_patterns": payload.get("exclude_patterns", []),
            "incremental": payload.get("incremental", False),
            "workers": payload.get("workers", 1),
            "extract_docstrings": payload.get("extract_docstrings", True),
            "calculate_complexity": payload.get("calculate_complexity", True),
            "analyze_dependencies": payload.get("analyze_dependencies", True),
            "min_complexity_threshold": payload.get("min_complexity_threshold", 0),
        }

        if not isinstance(validated["include_patterns"], list) or not all(
            isinstance(item, str) for item in validated["include_patterns"]
        ):
            raise ValueError("include_patterns must be list[str]")
        if not isinstance(validated["exclude_patterns"], list) or not all(
            isinstance(item, str) for item in validated["exclude_patterns"]
        ):
            raise ValueError("exclude_patterns must be list[str]")
        for pattern in [*validated["include_patterns"], *validated["exclude_patterns"]]:
            self._validate_glob(pattern)

        bool_fields = ("incremental", "extract_docstrings", "calculate_complexity", "analyze_dependencies")
        for field in bool_fields:
            if not isinstance(validated[field], bool):
                raise ValueError(f"{field} must be bool")

        if not isinstance(validated["workers"], int) or validated["workers"] < 1:
            raise ValueError("workers must be int >= 1")
        if not isinstance(validated["min_complexity_threshold"], int) or validated["min_complexity_threshold"] < 0:
            raise ValueError("min_complexity_threshold must be int >= 0")

        return validated

    def to_dict(self, config: ScanConfig) -> dict[str, Any]:
        data = asdict(config)
        data.pop("project_path", None)
        return data

    def from_dict(self, project_path: Path, payload: dict[str, Any]) -> ScanConfig:
        validated = self.validate(payload)
        return ScanConfig(project_path=project_path, **validated)

    def dump_yaml(self, config: ScanConfig) -> str:
        return cast(str, yaml.safe_dump(self.to_dict(config), allow_unicode=True, sort_keys=True))

    def load_yaml(self, project_path: Path, payload: str) -> ScanConfig:
        data = yaml.safe_load(payload) or {}
        if not isinstance(data, dict):
            raise ValueError("yaml payload must deserialize to object")
        return self.from_dict(project_path, data)

    def _validate_glob(self, pattern: str) -> None:
        if not pattern or not isinstance(pattern, str):
            raise ValueError("glob pattern must be non-empty string")
        if pattern.count("[") != pattern.count("]"):
            raise ValueError("glob pattern has unbalanced brackets")
