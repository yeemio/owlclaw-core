"""Schema validation and transformation bridge for LangChain integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jsonschema import Draft7Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError  # type: ignore[import-untyped]


class SchemaValidationError(ValueError):
    """Raised when input validation against JSON Schema fails."""


class SchemaBridge:
    """Bridge between OwlClaw payload shape and LangChain runnable inputs/outputs."""

    @staticmethod
    def validate_input(input_data: dict[str, Any], schema: dict[str, Any]) -> None:
        """Validate input data using JSON Schema Draft 7."""
        validator = Draft7Validator(schema)
        try:
            validator.validate(input_data)
        except JSONSchemaValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "$"
            raise SchemaValidationError(f"Input validation failed at '{location}': {exc.message}") from exc

    @staticmethod
    def transform_input(
        input_data: dict[str, Any],
        transformer: Callable[[dict[str, Any]], Any] | None = None,
    ) -> Any:
        """Transform OwlClaw input into runnable expected format."""
        if transformer is None:
            return input_data
        return transformer(input_data)

    @staticmethod
    def transform_output(
        output_data: Any,
        transformer: Callable[[Any], Any] | None = None,
    ) -> dict[str, Any]:
        """Transform runnable output into OwlClaw standard result format."""
        transformed = transformer(output_data) if transformer is not None else output_data
        if isinstance(transformed, dict):
            return transformed
        return {"result": transformed}
