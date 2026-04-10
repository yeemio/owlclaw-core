"""Validation utilities for OwlHub skill manifests and package layout."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from owlclaw.owlhub.schema import SkillManifest, ValidationError, ValidationResult

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_DEPENDENCY_CONSTRAINT_RE = re.compile(
    r"^(\^|~)?\d+\.\d+\.\d+$|^>=\d+\.\d+\.\d+,<\d+\.\d+\.\d+$"
)


class Validator:
    """Validate OwlHub skill metadata and package structure."""

    def validate_version(self, version: str) -> bool:
        """Return True when version matches semantic versioning."""
        if not isinstance(version, str):
            return False
        return _SEMVER_RE.fullmatch(version.strip()) is not None

    def validate_manifest(self, manifest: SkillManifest | dict[str, Any]) -> ValidationResult:
        """Validate required manifest fields and format constraints."""
        payload = self._to_manifest_payload(manifest)
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        required_fields = ["name", "version", "publisher", "description", "license"]
        for field in required_fields:
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(ValidationError(field=field, message=f"{field} is required"))

        name = payload.get("name")
        if isinstance(name, str) and name.strip() and _NAME_RE.fullmatch(name.strip()) is None:
            errors.append(ValidationError(field="name", message="name must be kebab-case"))

        publisher = payload.get("publisher")
        if isinstance(publisher, str) and publisher.strip() and _NAME_RE.fullmatch(publisher.strip()) is None:
            errors.append(ValidationError(field="publisher", message="publisher must be kebab-case"))

        description = payload.get("description")
        if isinstance(description, str) and description.strip():
            size = len(description.strip())
            if size < 10 or size > 500:
                errors.append(ValidationError(field="description", message="description length must be 10-500"))

        version = payload.get("version")
        if isinstance(version, str) and version.strip() and not self.validate_version(version):
            errors.append(ValidationError(field="version", message="version must be semver"))

        dependency_result = self.validate_dependencies(payload.get("dependencies", {}))
        errors.extend(dependency_result.errors)
        warnings.extend(dependency_result.warnings)

        return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)

    def validate_structure(self, skill_path: Path) -> ValidationResult:
        """Validate the minimal skill package directory structure."""
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []

        if not skill_path.exists():
            errors.append(ValidationError(field="path", message="skill path does not exist"))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)
        if not skill_path.is_dir():
            errors.append(ValidationError(field="path", message="skill path must be a directory"))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)
        if not (skill_path / "SKILL.md").exists():
            errors.append(ValidationError(field="SKILL.md", message="SKILL.md is required"))

        return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)

    def validate_dependencies(self, dependencies: Any) -> ValidationResult:
        """Validate dependency mapping and version constraint format."""
        errors: list[ValidationError] = []
        warnings: list[ValidationError] = []
        if dependencies is None:
            return ValidationResult(is_valid=True, errors=errors, warnings=warnings)
        if not isinstance(dependencies, dict):
            errors.append(
                ValidationError(field="dependencies", message="dependencies must be a mapping")
            )
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)

        for name, constraint in dependencies.items():
            if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
                errors.append(ValidationError(field="dependencies", message="dependency name must be kebab-case"))
                continue
            if not isinstance(constraint, str) or _DEPENDENCY_CONSTRAINT_RE.fullmatch(constraint.strip()) is None:
                errors.append(
                    ValidationError(
                        field=f"dependencies.{name}",
                        message="invalid version constraint",
                    )
                )

        return ValidationResult(is_valid=len(errors) == 0, errors=errors, warnings=warnings)

    @staticmethod
    def _to_manifest_payload(manifest: SkillManifest | dict[str, Any]) -> dict[str, Any]:
        if isinstance(manifest, SkillManifest):
            return {
                "name": manifest.name,
                "version": manifest.version,
                "publisher": manifest.publisher,
                "description": manifest.description,
                "license": manifest.license,
                "dependencies": manifest.dependencies,
                "tags": manifest.tags,
            }
        return dict(manifest)

