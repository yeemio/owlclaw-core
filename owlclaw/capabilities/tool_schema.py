"""Utilities for normalizing SKILL.md tool declarations into JSON Schema."""

from __future__ import annotations

from typing import Any

_SIMPLE_TYPES = {"string", "number", "integer", "boolean", "array", "object"}
_RESERVED_TOOL_FIELDS = {"description", "binding", "parameters", "params"}


def _is_object_schema(value: Any) -> bool:
    return isinstance(value, dict) and value.get("type") == "object" and isinstance(value.get("properties"), dict)


def _normalize_parameter_field(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    if _is_object_schema(value):
        return dict(value), None
    if not isinstance(value, dict):
        return None, "tool parameters must be a mapping/object"

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, spec in value.items():
        if not isinstance(name, str) or not name.strip():
            return None, "tool parameter names must be non-empty strings"
        normalized_name = name.strip()
        schema: dict[str, Any]
        if isinstance(spec, str):
            type_name = spec.strip().lower()
            if type_name not in _SIMPLE_TYPES:
                return None, f"unsupported simplified parameter type: {type_name}"
            schema = {"type": type_name}
        elif isinstance(spec, dict):
            raw_type = spec.get("type")
            if not isinstance(raw_type, str):
                return None, f"unsupported simplified parameter type: {raw_type!r}"
            type_name = raw_type.strip().lower()
            if type_name not in _SIMPLE_TYPES:
                return None, f"unsupported simplified parameter type: {raw_type!r}"
            schema = dict(spec)
            schema["type"] = type_name
        else:
            return None, "tool parameter spec must be string type or object"
        properties[normalized_name] = schema
        required.append(normalized_name)

    return {"type": "object", "properties": properties, "required": required}, None


def normalize_tools_schema(raw_tools: Any) -> tuple[dict[str, Any], list[str]]:
    """Normalize tool declarations to canonical tools_schema format.

    Supports both full schema and simplified declarations:

    tools:
      fetch_order:
        order_id: string
    """
    if raw_tools is None:
        return {}, []
    if not isinstance(raw_tools, dict):
        return {}, ["tools declaration must be a mapping/object"]

    normalized: dict[str, Any] = {}
    errors: list[str] = []

    for tool_name, tool_def in raw_tools.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            errors.append("tool name must be a non-empty string")
            continue
        normalized_name = tool_name.strip()

        if not isinstance(tool_def, dict):
            errors.append(f"tool '{normalized_name}' definition must be a mapping/object")
            continue

        entry: dict[str, Any] = {}
        description = tool_def.get("description")
        if isinstance(description, str):
            entry["description"] = description

        binding = tool_def.get("binding")
        if isinstance(binding, dict):
            entry["binding"] = dict(binding)

        schema_candidates = []
        if "parameters" in tool_def:
            schema_candidates.append(tool_def.get("parameters"))
        if "params" in tool_def:
            schema_candidates.append(tool_def.get("params"))
        inline_params = {k: v for k, v in tool_def.items() if k not in _RESERVED_TOOL_FIELDS}
        if inline_params:
            schema_candidates.append(inline_params)

        parameters: dict[str, Any] = {}
        parse_error: str | None = None

        # Full JSON Schema takes precedence over simplified declarations.
        full_schema = next((candidate for candidate in schema_candidates if _is_object_schema(candidate)), None)
        if full_schema is not None:
            parameters = dict(full_schema)
        elif schema_candidates:
            for candidate in schema_candidates:
                schema, err = _normalize_parameter_field(candidate)
                if schema is not None:
                    parameters = schema
                    parse_error = None
                    break
                parse_error = err
        if parse_error:
            errors.append(f"tool '{normalized_name}' {parse_error}")
            continue

        entry["parameters"] = parameters
        normalized[normalized_name] = entry

    return normalized, errors


def extract_tools_schema(frontmatter: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Extract canonical tools schema from frontmatter.

    Priority:
    1) metadata.tools_schema
    2) top-level tools_schema
    3) metadata.tools
    4) top-level tools
    """
    metadata = frontmatter.get("metadata")
    metadata_map = metadata if isinstance(metadata, dict) else {}

    for candidate in (
        metadata_map.get("tools_schema"),
        frontmatter.get("tools_schema"),
        metadata_map.get("tools"),
        frontmatter.get("tools"),
    ):
        if candidate is not None:
            return normalize_tools_schema(candidate)

    return {}, []
