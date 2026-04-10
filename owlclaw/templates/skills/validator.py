"""Template validator — validates .md.j2 templates and generated SKILL.md files."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from croniter import croniter  # type: ignore[import-untyped]
from jinja2 import Environment

from owlclaw.capabilities.tool_schema import extract_tools_schema
from owlclaw.templates.skills.models import ValidationError

logger = logging.getLogger(__name__)

# Pattern for metadata comment block
_METADATA_BLOCK = re.compile(r"\{#.*?#\}", re.DOTALL)

# Supported trigger patterns: cron("..."), webhook("..."), queue("...")
_CRON_PATTERN = re.compile(r'^cron\("([^"]+)"\)$')
_WEBHOOK_PATTERN = re.compile(r'^webhook\("([^"]+)"\)$')
_QUEUE_PATTERN = re.compile(r'^queue\("([^"]+)"\)$')
_QUEUE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class TemplateValidator:
    """Validates .md.j2 template files and generated SKILL.md files."""

    def validate_template(self, template_path: Path) -> list[ValidationError]:
        """Validate a template file.

        Args:
            template_path: Path to the .md.j2 template file.

        Returns:
            List of validation errors (empty if valid).
        """
        errors: list[ValidationError] = []
        try:
            content = template_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Cannot read template file: path=%s, error=%s", template_path, e)
            errors.append(
                ValidationError(
                    field="file",
                    message=f"Cannot read file: {e}",
                    severity="error",
                )
            )
            return errors

        if not _METADATA_BLOCK.search(content):
            errors.append(
                ValidationError(
                    field="metadata",
                    message="Template missing metadata comment block {# ... #}",
                    severity="error",
                )
            )
        else:
            try:
                match = _METADATA_BLOCK.search(content)
                assert match is not None  # guarded above
                metadata_raw = match.group(0)[2:-2].strip()
                loaded = yaml.safe_load(metadata_raw)
                if not isinstance(loaded, dict):
                    errors.append(
                        ValidationError(
                            field="metadata",
                            message="Template metadata must be a YAML mapping",
                            severity="error",
                        )
                    )
                else:
                    for required in ("name", "description", "tags", "parameters"):
                        if required not in loaded:
                            errors.append(
                                ValidationError(
                                    field="metadata",
                                    message=f"Template metadata missing required field: {required}",
                                    severity="error",
                                )
                            )
                    if "tags" in loaded and not isinstance(loaded["tags"], list):
                        errors.append(
                            ValidationError(
                                field="metadata.tags",
                                message="Template metadata field 'tags' must be a list",
                                severity="error",
                            )
                        )
                    if "parameters" in loaded and not isinstance(loaded["parameters"], list):
                        errors.append(
                            ValidationError(
                                field="metadata.parameters",
                                message="Template metadata field 'parameters' must be a list",
                                severity="error",
                            )
                        )
                    elif isinstance(loaded.get("parameters"), list):
                        allowed_types = {"str", "int", "bool", "list"}
                        for idx, param in enumerate(loaded["parameters"]):
                            field_prefix = f"metadata.parameters[{idx}]"
                            if not isinstance(param, dict):
                                errors.append(
                                    ValidationError(
                                        field=field_prefix,
                                        message="Parameter definition must be a mapping/object",
                                        severity="error",
                                    )
                                )
                                continue
                            name = param.get("name")
                            if not isinstance(name, str) or not name.strip():
                                errors.append(
                                    ValidationError(
                                        field=f"{field_prefix}.name",
                                        message="Parameter name must be a non-empty string",
                                        severity="error",
                                    )
                                )
                            p_type = str(param.get("type", "str")).strip().lower()
                            if p_type not in allowed_types:
                                errors.append(
                                    ValidationError(
                                        field=f"{field_prefix}.type",
                                        message="Parameter type must be one of: str, int, bool, list",
                                        severity="error",
                                    )
                                )
                    if self._contains_jinja_placeholder(loaded):
                        errors.append(
                            ValidationError(
                                field="metadata",
                                message="Template metadata contains unrendered Jinja2 placeholders",
                                severity="error",
                            )
                        )
            except yaml.YAMLError as e:
                errors.append(
                    ValidationError(
                        field="metadata",
                        message=f"Invalid metadata YAML: {e}",
                        severity="error",
                    )
                )

        try:
            env = Environment()
            env.parse(content)
        except Exception as e:
            logger.debug("Invalid Jinja2 syntax in %s: %s", template_path, e)
            errors.append(
                ValidationError(
                    field="syntax",
                    message=f"Invalid Jinja2 syntax: {e}",
                    severity="error",
                )
            )

        return errors

    def validate_skill_file(self, skill_path: Path) -> list[ValidationError]:
        """Validate a generated SKILL.md file.

        Args:
            skill_path: Path to the SKILL.md file.

        Returns:
            List of validation errors (empty if valid).
        """
        errors: list[ValidationError] = []
        try:
            content = skill_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Cannot read skill file: path=%s, error=%s", skill_path, e)
            errors.append(
                ValidationError(
                    field="file",
                    message=f"Cannot read file: {e}",
                    severity="error",
                )
            )
            return errors

        try:
            frontmatter, body = self._parse_skill_file(content)
        except (yaml.YAMLError, ValueError) as e:
            errors.append(
                ValidationError(
                    field="frontmatter",
                    message=f"Invalid frontmatter: {e}",
                    severity="error",
                )
            )
            return errors
        errors.extend(self._validate_frontmatter(frontmatter))
        errors.extend(self._validate_body(body, frontmatter))
        return errors

    def _parse_skill_file(self, content: str) -> tuple[dict[str, Any], str]:
        """Parse SKILL.md content into frontmatter dict and body string."""
        content = content.lstrip("\ufeff")
        match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n(.*))?$", content, re.DOTALL)
        if not match:
            return {}, content

        frontmatter_str, body = match.groups()
        body = body or ""
        frontmatter: dict[str, Any] = {}
        if frontmatter_str.strip():
            loaded = yaml.safe_load(frontmatter_str)
            if loaded is None:
                frontmatter = {}
            elif isinstance(loaded, dict):
                frontmatter = loaded
            else:
                raise ValueError("Frontmatter must be a YAML mapping/object")
        return frontmatter, body

    def _validate_frontmatter(self, frontmatter: dict[str, Any]) -> list[ValidationError]:
        """Validate frontmatter fields."""
        errors: list[ValidationError] = []
        required_fields = ["name", "description"]
        for field in required_fields:
            if field not in frontmatter:
                errors.append(
                    ValidationError(
                        field=field,
                        message=f"Missing required field: {field}",
                        severity="error",
                    )
                )

        if "description" in frontmatter:
            description = frontmatter["description"]
            if not isinstance(description, str) or not description.strip():
                errors.append(
                    ValidationError(
                        field="description",
                        message="Description must be a non-empty string",
                        severity="error",
                    )
                )

        if "name" in frontmatter:
            name = frontmatter["name"]
            if not isinstance(name, str) or not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
                errors.append(
                    ValidationError(
                        field="name",
                        message=f"Name must be in kebab-case format: {name!r}",
                        severity="error",
                    )
                )

        if "owlclaw" in frontmatter:
            owlclaw = frontmatter["owlclaw"]
            if isinstance(owlclaw, dict):
                if "spec_version" not in owlclaw:
                    errors.append(
                        ValidationError(
                            field="owlclaw.spec_version",
                            message="Missing owlclaw.spec_version",
                            severity="warning",
                        )
                    )
                if "trigger" in owlclaw:
                    trigger = owlclaw["trigger"]
                    if not isinstance(trigger, str):
                        errors.append(
                            ValidationError(
                                field="owlclaw.trigger",
                                message="Trigger must be a string",
                                severity="error",
                            )
                        )
                    elif not self._validate_trigger_syntax(trigger):
                        errors.append(
                            ValidationError(
                                field="owlclaw.trigger",
                                message=f"Invalid trigger syntax: {trigger!r}",
                                severity="error",
                            )
                        )
                if "focus" in owlclaw:
                    focus = owlclaw["focus"]
                    if isinstance(focus, str):
                        if not focus.strip():
                            errors.append(
                                ValidationError(
                                    field="owlclaw.focus",
                                    message="Focus must not be empty",
                                    severity="error",
                                )
                            )
                    elif isinstance(focus, list):
                        if not focus:
                            errors.append(
                                ValidationError(
                                    field="owlclaw.focus",
                                    message="Focus list must not be empty",
                                    severity="error",
                                )
                            )
                        elif any(not isinstance(item, str) or not item.strip() for item in focus):
                            errors.append(
                                ValidationError(
                                    field="owlclaw.focus",
                                    message="Focus list items must be non-empty strings",
                                    severity="error",
                                )
                            )
                    else:
                        errors.append(
                            ValidationError(
                                field="owlclaw.focus",
                                message="Focus must be a string or list of strings",
                                severity="error",
                            )
                        )
                if "risk_level" in owlclaw:
                    risk_level = owlclaw["risk_level"]
                    if not isinstance(risk_level, str) or risk_level.strip().lower() not in {
                        "low",
                        "medium",
                        "high",
                        "critical",
                    }:
                        errors.append(
                            ValidationError(
                                field="owlclaw.risk_level",
                                message="Risk level must be one of: low, medium, high, critical",
                                severity="error",
                            )
                        )
                risk_level_val = owlclaw.get("risk_level")
                if "requires_confirmation" in owlclaw and not isinstance(owlclaw["requires_confirmation"], bool):
                    errors.append(
                        ValidationError(
                            field="owlclaw.requires_confirmation",
                            message="requires_confirmation must be a boolean",
                            severity="error",
                        )
                    )
                elif (
                    isinstance(risk_level_val, str)
                    and risk_level_val.strip().lower() in {"high", "critical"}
                    and owlclaw.get("requires_confirmation") is False
                ):
                    errors.append(
                        ValidationError(
                            field="owlclaw.requires_confirmation",
                            message="High/critical risk skills should generally require confirmation",
                            severity="warning",
                        )
                    )
            else:
                errors.append(
                    ValidationError(
                        field="owlclaw",
                        message="owlclaw must be a mapping/object",
                        severity="error",
                    )
                )

        if self._contains_jinja_placeholder(frontmatter):
            errors.append(
                ValidationError(
                    field="frontmatter",
                    message="Frontmatter contains unrendered Jinja2 placeholders",
                    severity="error",
                )
            )

        return errors

    def _validate_body(self, body: str, frontmatter: dict[str, Any]) -> list[ValidationError]:
        """Validate Markdown body."""
        errors: list[ValidationError] = []
        if not body.strip():
            errors.append(
                ValidationError(
                    field="body",
                    message="Body is empty",
                    severity="error",
                )
            )
        elif not re.search(r"^#+\s+", body, re.MULTILINE):
            errors.append(
                ValidationError(
                    field="body",
                    message="Body should contain at least one heading",
                    severity="warning",
                )
            )
        # Detect unrendered Jinja2 placeholders in generated SKILL.md.
        if re.search(r"\{\{.*?\}\}|\{%.*?%\}", body, re.DOTALL):
            errors.append(
                ValidationError(
                    field="body",
                    message="Body contains unrendered Jinja2 placeholders",
                    severity="error",
                )
            )
        declared_tools = self._extract_tools_from_body(body)
        if declared_tools:
            tools_schema, _tool_errors = extract_tools_schema(frontmatter)
            known_tools = {name for name in tools_schema if isinstance(name, str)}
            missing = sorted(tool for tool in declared_tools if tool not in known_tools)
            if missing:
                errors.append(
                    ValidationError(
                        field="body.tools",
                        message=f"Referenced tools missing from frontmatter tools schema: {', '.join(missing)}",
                        severity="warning",
                    )
                )
        return errors

    @staticmethod
    def _extract_tools_from_body(body: str) -> set[str]:
        tools: set[str] = set()
        for line in body.splitlines():
            stripped = line.strip()
            match = re.match(r"^[-*]\s*`?([a-zA-Z0-9_-]+)`?\(", stripped)
            if match:
                tools.add(match.group(1))
        return tools

    def _validate_trigger_syntax(self, trigger: str) -> bool:
        """Check if trigger matches supported syntax (cron/webhook/queue)."""
        cron_match = _CRON_PATTERN.match(trigger)
        if cron_match:
            expr = cron_match.group(1).strip()
            return bool(expr) and croniter.is_valid(expr)

        webhook_match = _WEBHOOK_PATTERN.match(trigger)
        if webhook_match:
            path = webhook_match.group(1).strip()
            return bool(path) and path.startswith("/") and not any(ch.isspace() for ch in path)

        queue_match = _QUEUE_PATTERN.match(trigger)
        if queue_match:
            queue_name = queue_match.group(1).strip()
            return bool(queue_name) and bool(_QUEUE_NAME_PATTERN.match(queue_name))

        return False

    def _contains_jinja_placeholder(self, value: Any) -> bool:
        """Recursively detect Jinja2 placeholders in parsed YAML values."""
        if isinstance(value, str):
            return bool(re.search(r"\{\{.*?\}\}|\{%.*?%\}", value, re.DOTALL))
        if isinstance(value, dict):
            return any(self._contains_jinja_placeholder(v) for v in value.values())
        if isinstance(value, list):
            return any(self._contains_jinja_placeholder(v) for v in value)
        return False

    def validate_and_report(
        self,
        template_path: Path | None = None,
        skill_path: Path | None = None,
    ) -> str:
        """Validate and return a human-readable error report.

        Args:
            template_path: Optional path to .md.j2 template.
            skill_path: Optional path to SKILL.md file.

        Returns:
            Report string (empty if no errors).
        """
        errors: list[ValidationError] = []
        if template_path:
            errors.extend(self.validate_template(template_path))
        if skill_path:
            errors.extend(self.validate_skill_file(skill_path))

        if not errors:
            return ""

        lines = ["Validation report:"]
        for e in errors:
            lines.append(f"  [{e.severity}] {e.field}: {e.message}")
        return "\n".join(lines)
