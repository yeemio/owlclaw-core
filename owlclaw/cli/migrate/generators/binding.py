"""Declarative binding SKILL.md generator for cli-migrate."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml  # type: ignore[import-untyped]


def _kebab(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return normalized or "generated-skill"


def _snake_upper(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", value)
    if not parts:
        return "API_TOKEN"
    return "_".join(part.upper() for part in parts)


def _operation_name(endpoint: OpenAPIEndpoint) -> str:
    if endpoint.operation_id.strip():
        return _kebab(endpoint.operation_id)
    summary = endpoint.summary.strip() or f"{endpoint.method}-{endpoint.path}"
    return _kebab(summary)


def _description(endpoint: OpenAPIEndpoint) -> str:
    if endpoint.description.strip():
        return endpoint.description.strip()
    if endpoint.summary.strip():
        return endpoint.summary.strip()
    return f"{endpoint.method.upper()} {endpoint.path}"


@dataclass(slots=True)
class OpenAPIEndpoint:
    """Normalized OpenAPI endpoint descriptor for code generation."""

    method: str
    path: str
    operation_id: str = ""
    summary: str = ""
    description: str = ""
    parameters: list[dict[str, Any]] = field(default_factory=list)
    request_body: dict[str, Any] = field(default_factory=dict)
    responses: dict[str, Any] = field(default_factory=dict)
    security: list[dict[str, list[str]]] = field(default_factory=list)
    security_schemes: dict[str, dict[str, Any]] = field(default_factory=dict)
    server_url: str = ""


@dataclass(slots=True)
class ORMOperation:
    """Minimal ORM operation descriptor for SQL binding generation."""

    model_name: str
    table_name: str
    columns: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    connection_env: str = "READ_DB_DSN"


@dataclass(slots=True)
class BindingGenerationResult:
    """Generated binding skill output."""

    skill_name: str
    skill_content: str
    binding_type: str
    tools_count: int
    prerequisites_env: list[str]
    warnings: list[str] = field(default_factory=list)


class BindingGenerator:
    """Generate SKILL.md content with declarative bindings."""

    def generate_from_openapi(self, endpoint: OpenAPIEndpoint) -> BindingGenerationResult:
        skill_name = _operation_name(endpoint)
        tool_name = skill_name
        parameters_schema = self._build_openapi_parameters_schema(endpoint)
        headers, prerequisite_env, warnings = self._build_security_headers(endpoint)
        binding = {
            "type": "http",
            "method": endpoint.method.upper(),
            "url": self._compose_url(endpoint),
            "headers": headers,
            "response_mapping": self._build_response_mapping(endpoint),
        }
        frontmatter = {
            "name": skill_name,
            "description": _description(endpoint),
            "metadata": {
                "tools_schema": {
                    tool_name: {
                        "description": _description(endpoint),
                        "parameters": parameters_schema,
                        "binding": binding,
                    }
                }
            },
            "owlclaw": {"prerequisites": {"env": sorted(prerequisite_env)}},
        }
        content = self._render_skill_markdown(frontmatter, skill_name)
        return BindingGenerationResult(
            skill_name=skill_name,
            skill_content=content,
            binding_type="http",
            tools_count=1,
            prerequisites_env=sorted(prerequisite_env),
            warnings=warnings,
        )

    def generate_from_orm(self, operation: ORMOperation) -> BindingGenerationResult:
        skill_name = _kebab(f"{operation.model_name}-query")
        placeholders = [f":{name}" for name in operation.filters]
        where_clause = ""
        if operation.filters:
            terms = [f"{name} = {placeholder}" for name, placeholder in zip(operation.filters, placeholders, strict=False)]
            where_clause = " WHERE " + " AND ".join(terms)
        columns = ", ".join(operation.columns) if operation.columns else "*"
        query = f"SELECT {columns} FROM {operation.table_name}{where_clause}"
        params_schema = {
            "type": "object",
            "properties": {name: {"type": "string"} for name in operation.filters},
            "required": list(operation.filters),
        }
        parameter_mapping = {name: name for name in operation.filters}
        frontmatter = {
            "name": skill_name,
            "description": f"Query {operation.table_name} records",
            "metadata": {
                "tools_schema": {
                    skill_name: {
                        "description": f"Query {operation.table_name}",
                        "parameters": params_schema,
                        "binding": {
                            "type": "sql",
                            "connection": f"${{{operation.connection_env}}}",
                            "query": query,
                            "read_only": True,
                            "parameter_mapping": parameter_mapping,
                        },
                    }
                }
            },
            "owlclaw": {"prerequisites": {"env": [operation.connection_env]}},
        }
        content = self._render_skill_markdown(frontmatter, skill_name)
        return BindingGenerationResult(
            skill_name=skill_name,
            skill_content=content,
            binding_type="sql",
            tools_count=1,
            prerequisites_env=[operation.connection_env],
        )

    def generate_mcp_tool_definition(self, endpoint: OpenAPIEndpoint) -> dict[str, Any]:
        """Generate one MCP tool definition payload from OpenAPI endpoint."""
        tool_name = _operation_name(endpoint)
        headers, prerequisite_env, _warnings = self._build_security_headers(endpoint)
        return {
            "name": tool_name,
            "description": _description(endpoint),
            "inputSchema": self._build_openapi_parameters_schema(endpoint),
            "binding": {
                "type": "http",
                "method": endpoint.method.upper(),
                "url": self._compose_url(endpoint),
                "headers": headers,
            },
            "prerequisites": {
                "env": sorted(prerequisite_env),
            },
        }

    def _compose_url(self, endpoint: OpenAPIEndpoint) -> str:
        base = endpoint.server_url.rstrip("/")
        if base:
            return f"{base}{endpoint.path}"
        return endpoint.path

    def _build_openapi_parameters_schema(self, endpoint: OpenAPIEndpoint) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in endpoint.parameters:
            name = str(param.get("name", "")).strip()
            if not name:
                continue
            schema = param.get("schema", {})
            p_type = str(schema.get("type", "string")).lower()
            if p_type not in {"string", "number", "integer", "boolean", "array", "object"}:
                p_type = "string"
            properties[name] = {"type": p_type}
            if bool(param.get("required", False)):
                required.append(name)

        body = endpoint.request_body.get("content", {})
        json_body = body.get("application/json", {})
        body_schema = json_body.get("schema", {})
        if isinstance(body_schema, dict):
            body_props = body_schema.get("properties", {})
            if isinstance(body_props, dict):
                for name, spec in body_props.items():
                    if not isinstance(name, str):
                        continue
                    p_type = str((spec or {}).get("type", "string")).lower() if isinstance(spec, dict) else "string"
                    if p_type not in {"string", "number", "integer", "boolean", "array", "object"}:
                        p_type = "string"
                    properties[name] = {"type": p_type}
            body_required = body_schema.get("required", [])
            if isinstance(body_required, list):
                for item in body_required:
                    if isinstance(item, str) and item not in required:
                        required.append(item)

        return {"type": "object", "properties": properties, "required": required}

    def _build_security_headers(self, endpoint: OpenAPIEndpoint) -> tuple[dict[str, str], set[str], list[str]]:
        headers: dict[str, str] = {}
        env_set: set[str] = set()
        warnings: list[str] = []
        for sec in endpoint.security:
            for scheme_name in sec:
                scheme = endpoint.security_schemes.get(scheme_name, {})
                env_name = f"{_snake_upper(scheme_name)}_TOKEN"
                scheme_type = str(scheme.get("type", "")).lower()
                if scheme_type == "apikey":
                    env_name = f"{_snake_upper(scheme_name)}_API_KEY"
                    in_value = str(scheme.get("in", "header")).lower()
                    key_name = str(scheme.get("name", "X-API-Key"))
                    if in_value == "header":
                        headers[key_name] = f"${{{env_name}}}"
                    else:
                        warnings.append(f"security scheme '{scheme_name}' in={in_value} is not mapped to header")
                else:
                    # http bearer/oauth2/openIdConnect fallback to Authorization header
                    headers["Authorization"] = f"Bearer ${{{env_name}}}"
                env_set.add(env_name)
        return headers, env_set, warnings

    def _build_response_mapping(self, endpoint: OpenAPIEndpoint) -> dict[str, Any]:
        status_codes: dict[str, str] = {}
        for status in endpoint.responses:
            code = str(status)
            if code.startswith("2"):
                status_codes[code] = "success"
            elif code == "404":
                status_codes[code] = "not_found"
            elif code == "429":
                status_codes[code] = "rate_limited"
            elif code.startswith("4") or code.startswith("5"):
                status_codes[code] = "error"

        path = "$"
        success_payload = None
        for code in ("200", "201", "202"):
            success_payload = endpoint.responses.get(code)
            if success_payload is not None:
                break
        if isinstance(success_payload, dict):
            content = success_payload.get("content", {})
            if isinstance(content, dict):
                json_content = content.get("application/json", {})
                if isinstance(json_content, dict):
                    schema = json_content.get("schema", {})
                    if isinstance(schema, dict):
                        props = schema.get("properties", {})
                        if isinstance(props, dict) and "data" in props:
                            path = "$.data"
        return {"path": path, "status_codes": status_codes}

    def _render_skill_markdown(self, frontmatter: dict[str, Any], skill_name: str) -> str:
        header = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
        body = (
            "# Instructions\n\n"
            "## Business Rules Placeholder\n\n"
            "Describe business decision rules in natural language here.\n\n"
            "## Execution Notes\n\n"
            f"- Generated by cli-migrate binding generator for `{skill_name}`.\n"
            "- Review parameter meanings and edge-case handling before production use.\n"
        )
        return f"---\n{header}\n---\n\n{body}"
