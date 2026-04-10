"""Template renderer — Jinja2-based rendering with parameter validation."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from owlclaw.templates.skills.exceptions import (
    MissingParameterError,
    ParameterTypeError,
    ParameterValueError,
    TemplateNotFoundError,
    TemplateRenderError,
)
from owlclaw.templates.skills.models import TemplateParameter
from owlclaw.templates.skills.registry import TemplateRegistry

logger = logging.getLogger(__name__)


class TemplateRenderer:
    """Renders .md.j2 templates to SKILL.md content with parameter validation."""

    def __init__(self, registry: TemplateRegistry) -> None:
        """Initialize renderer with a template registry.

        Args:
            registry: Template registry (provides templates_dir and metadata).
        """
        self.registry = registry
        self.templates_dir = Path(registry.templates_dir)
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
        )
        self.env.filters["kebab_case"] = self._kebab_case
        self.env.filters["snake_case"] = self._snake_case

    def _get_template_path(self, template_id: str) -> Path:
        """Resolve template_id to file path. Format: category/name -> category/name.md.j2."""
        return self.templates_dir / f"{template_id}.md.j2"

    def _validate_parameters(
        self,
        params: dict[str, Any],
        param_defs: list[TemplateParameter],
    ) -> None:
        """Raise MissingParameterError if required parameters are missing."""
        missing = [p.name for p in param_defs if p.required and params.get(p.name) is None]
        if missing:
            raise MissingParameterError(
                f"Missing required parameters: {', '.join(missing)}",
                missing=missing,
            )

    def _validate_unknown_parameters(
        self,
        params: dict[str, Any],
        param_defs: list[TemplateParameter],
    ) -> None:
        allowed = {p.name for p in param_defs}
        unknown = sorted(k for k in params if k not in allowed)
        if unknown:
            raise ParameterValueError(
                f"Unknown template parameters: {', '.join(unknown)}",
                param_name=unknown[0],
                value=repr(params.get(unknown[0])),
            )

    def _apply_defaults(
        self,
        params: dict[str, Any],
        param_defs: list[TemplateParameter],
    ) -> dict[str, Any]:
        """Apply default values for parameters not provided."""
        result = dict(params)
        for p in param_defs:
            if p.name not in result and p.default is not None:
                result[p.name] = p.default
        return result

    def _validate_and_convert_parameters(
        self,
        params: dict[str, Any],
        param_defs: list[TemplateParameter],
    ) -> dict[str, Any]:
        """Convert parameter values to expected types."""
        result: dict[str, Any] = {}
        for p in param_defs:
            if p.name not in params:
                continue
            val = params[p.name]
            try:
                if p.type == "int":
                    if isinstance(val, bool):
                        raise ValueError("bool is not a valid int parameter value")
                    result[p.name] = int(val)
                elif p.type == "bool":
                    if isinstance(val, bool):
                        result[p.name] = val
                    elif isinstance(val, int) and val in (0, 1):
                        result[p.name] = bool(val)
                    elif isinstance(val, str):
                        normalized = val.strip().lower()
                        if normalized in ("1", "true", "yes", "on"):
                            result[p.name] = True
                        elif normalized in ("0", "false", "no", "off"):
                            result[p.name] = False
                        else:
                            raise ValueError(f"invalid bool literal: {val!r}")
                    else:
                        raise ValueError(f"invalid bool type: {type(val).__name__}")
                elif p.type == "list":
                    if isinstance(val, list):
                        result[p.name] = val
                    elif isinstance(val, str):
                        stripped = val.strip()
                        if stripped.startswith("[") and stripped.endswith("]"):
                            parsed = yaml.safe_load(stripped)
                            if isinstance(parsed, list):
                                result[p.name] = parsed
                            else:
                                parts = [item.strip() for item in val.split(",")]
                                result[p.name] = [item for item in parts if item]
                        else:
                            parts = [item.strip() for item in val.split(",")]
                            result[p.name] = [item for item in parts if item]
                    else:
                        result[p.name] = [val]
                else:
                    result[p.name] = str(val)
            except (TypeError, ValueError) as e:
                raise ParameterTypeError(
                    f"Parameter '{p.name}': expected {p.type}, got {type(val).__name__}",
                    param_name=p.name,
                    expected=p.type,
                    got=type(val).__name__,
                ) from e
        return result

    def _validate_parameter_choices(
        self,
        params: dict[str, Any],
        param_defs: list[TemplateParameter],
    ) -> None:
        """Raise ParameterValueError if a value is not in choices."""
        for p in param_defs:
            if p.choices is None or p.name not in params:
                continue
            val = params[p.name]
            if isinstance(val, str) and all(isinstance(choice, str) for choice in p.choices):
                normalized = val.strip().lower()
                matched = next((choice for choice in p.choices if str(choice).lower() == normalized), None)
                if matched is not None:
                    params[p.name] = matched
                    continue
            if val not in p.choices:
                raise ParameterValueError(
                    f"Parameter '{p.name}' value {val!r} not in {p.choices}",
                    param_name=p.name,
                    value=repr(val),
                    choices=p.choices,
                )

    def render(self, template_id: str, parameters: dict[str, Any] | None = None) -> str:
        """Render template to SKILL.md content.

        Args:
            template_id: Template ID (e.g. "monitoring/health-check").
            parameters: Template parameters (validated and defaults applied).

        Returns:
            Rendered SKILL.md content.

        Raises:
            TemplateNotFoundError: Template not found.
            MissingParameterError: Required parameter missing.
            ParameterTypeError: Parameter type mismatch.
            ParameterValueError: Parameter value not in choices.
            TemplateRenderError: Jinja2 render error.
        """
        parameters = parameters or {}
        logger.debug("Rendering template %s with %d params", template_id, len(parameters))
        metadata = self.registry.get_template_or_raise(template_id)
        self._validate_unknown_parameters(parameters, metadata.parameters)
        params = self._apply_defaults(parameters, metadata.parameters)
        self._validate_parameters(params, metadata.parameters)
        converted = self._validate_and_convert_parameters(params, metadata.parameters)
        params = {**params, **converted}
        self._validate_parameter_choices(params, metadata.parameters)

        template_name = f"{template_id}.md.j2"

        try:
            template = self.env.get_template(template_name)
            return template.render(**params)
        except TemplateNotFound as e:
            logger.warning("Template not found: %s", template_id, exc_info=False)
            raise TemplateNotFoundError(str(e)) from e
        except Exception as e:
            logger.exception("Template render failed: template_id=%s", template_id)
            raise TemplateRenderError(f"Template render failed: {e}") from e

    @staticmethod
    def _kebab_case(text: str) -> str:
        """Convert to kebab-case (lowercase, spaces/underscores to hyphens)."""
        if not isinstance(text, str):
            text = str(text)
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s_-]", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        text = re.sub(r"-+", "-", text)
        return text.strip("-").lower()

    @staticmethod
    def _snake_case(text: str) -> str:
        """Convert to snake_case (lowercase, spaces/hyphens to underscores)."""
        if not isinstance(text, str):
            text = str(text)
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s_-]", "", text)
        text = re.sub(r"[\s-]+", "_", text)
        text = re.sub(r"_+", "_", text)
        return text.strip("_").lower()
