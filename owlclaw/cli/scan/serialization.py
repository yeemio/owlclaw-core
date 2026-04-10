"""Serialization and schema validation for cli-scan results."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from owlclaw.cli.scan.models import ScanResult


class ResultSerializer(ABC):
    """Serialization interface for scan results."""

    @abstractmethod
    def serialize(self, result: ScanResult) -> str:
        raise NotImplementedError


class JSONSerializer(ResultSerializer):
    """Serialize ScanResult to JSON."""

    def __init__(self, pretty: bool = False) -> None:
        self.pretty = pretty

    def serialize(self, result: ScanResult) -> str:
        indent = 2 if self.pretty else None
        return json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, indent=indent)


class YAMLSerializer(ResultSerializer):
    """Serialize ScanResult to YAML."""

    def serialize(self, result: ScanResult) -> str:
        return cast(str, yaml.safe_dump(result.to_dict(), allow_unicode=True, sort_keys=True))


class SchemaValidator:
    """Validate scan result payload shape against expected schema."""

    def validate(self, payload: dict[str, Any]) -> tuple[bool, list[str]]:
        errors: list[str] = []
        if not isinstance(payload, dict):
            return False, ["payload must be object"]

        metadata = payload.get("metadata")
        files = payload.get("files")
        if not isinstance(metadata, dict):
            errors.append("metadata must be object")
        else:
            self._validate_metadata(metadata, errors)

        if not isinstance(files, dict):
            errors.append("files must be object")
        else:
            for key, value in files.items():
                if not isinstance(key, str):
                    errors.append("file key must be string")
                    continue
                if not isinstance(value, dict):
                    errors.append(f"file '{key}' must be object")
                    continue
                self._validate_file_result(key, value, errors)

        return len(errors) == 0, errors

    def _validate_metadata(self, metadata: dict[str, Any], errors: list[str]) -> None:
        if not isinstance(metadata.get("project_path"), str):
            errors.append("metadata.project_path has invalid type")
        if not isinstance(metadata.get("scanned_files"), int):
            errors.append("metadata.scanned_files has invalid type")
        if not isinstance(metadata.get("failed_files"), int):
            errors.append("metadata.failed_files has invalid type")
        if not isinstance(metadata.get("scan_time_seconds"), int | float):
            errors.append("metadata.scan_time_seconds has invalid type")

    def _validate_file_result(self, key: str, value: dict[str, Any], errors: list[str]) -> None:
        if not isinstance(value.get("file_path"), str):
            errors.append(f"files.{key}.file_path must be string")
        for field in ("functions", "imports", "errors"):
            if not isinstance(value.get(field), list):
                errors.append(f"files.{key}.{field} must be list")
