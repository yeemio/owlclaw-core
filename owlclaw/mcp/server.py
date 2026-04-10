"""Minimal MCP protocol server for OwlClaw capabilities and skills."""

from __future__ import annotations

import hmac
import inspect
import json
import logging
import os
import types
from dataclasses import dataclass
from typing import Any, Union, get_args, get_origin

from owlclaw.app import OwlClaw
from owlclaw.capabilities.registry import CapabilityRegistry
from owlclaw.capabilities.skills import Skill, SkillsLoader
from owlclaw.triggers.signal.models import Signal, SignalSource, SignalType
from owlclaw.triggers.signal.router import SignalRouter

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"


@dataclass(frozen=True)
class ResourceRef:
    """Mapped resource reference for one skill file."""

    uri: str
    skill: Skill


class McpRequestError(ValueError):
    """Typed MCP request error with explicit JSON-RPC code."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class McpProtocolServer:
    """Process JSON-RPC MCP requests against OwlClaw registry and skills."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        skills_loader: SkillsLoader,
        signal_router: SignalRouter | None = None,
        auth_token: str | None = None,
    ):
        self.registry = registry
        self.skills_loader = skills_loader
        self.signal_router = signal_router
        token = auth_token
        if token is None:
            token = os.getenv("OWLCLAW_MCP_TOKEN")
        self._auth_token = token.strip() if isinstance(token, str) and token.strip() else None
        self._is_authenticated = self._auth_token is None
        self._resource_cache: dict[str, ResourceRef] = {}
        self._refresh_resource_cache()

    @classmethod
    def from_app(cls, app: OwlClaw) -> McpProtocolServer:
        """Create server from an OwlClaw app with mounted skills."""
        if app.registry is None or app.skills_loader is None:
            raise ValueError("app must call mount_skills() before creating MCP server")
        return cls(registry=app.registry, skills_loader=app.skills_loader)

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Handle one JSON-RPC message."""
        request_id = message.get("id")
        try:
            method = self._validate_request(message)
            params = message.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                return self._error(request_id, -32602, "params must be an object")

            auth_error = self._enforce_auth(method, params)
            if auth_error is not None:
                return self._error(request_id, -32003, auth_error)

            if method == "initialize":
                result = self._handle_initialize()
            elif method == "tools/list":
                result = self._handle_tools_list()
            elif method == "tools/call":
                result = await self._handle_tools_call(params)
            elif method == "resources/list":
                result = self._handle_resources_list()
            elif method == "resources/read":
                result = self._handle_resources_read(params)
            else:
                return self._error(request_id, -32601, f"method not found: {method}")
            return self._success(request_id, result)
        except McpRequestError as exc:
            return self._error(request_id, exc.code, exc.message)
        except ValueError as exc:
            return self._error(request_id, -32600, str(exc))
        except KeyError as exc:
            return self._error(request_id, -32602, f"missing required field: {exc.args[0]}")
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("Unhandled MCP server error")
            return self._error(request_id, -32005, "execution error")

    async def process_stdio_line(self, line: str) -> str:
        """Parse one input line and return JSON-RPC response line."""
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            return json.dumps(self._error(None, -32700, f"parse error: {exc.msg}"), ensure_ascii=False)
        if not isinstance(parsed, dict):
            return json.dumps(self._error(None, -32600, "request must be an object"), ensure_ascii=False)
        response = await self.handle_message(parsed)
        return json.dumps(response, ensure_ascii=False)

    def _validate_request(self, message: dict[str, Any]) -> str:
        if message.get("jsonrpc") != JSONRPC_VERSION:
            raise ValueError("invalid jsonrpc version")
        method = message.get("method")
        if not isinstance(method, str) or not method.strip():
            raise ValueError("method must be a non-empty string")
        return method

    def _enforce_auth(self, method: str, params: dict[str, Any]) -> str | None:
        if self._auth_token is None:
            return None
        if method == "initialize":
            token = params.get("token")
            if not isinstance(token, str) or not token.strip():
                return "unauthorized: invalid or missing MCP token"
            if not hmac.compare_digest(token.strip().encode("utf-8"), self._auth_token.encode("utf-8")):
                return "unauthorized: invalid or missing MCP token"
            self._is_authenticated = True
            return None
        if not self._is_authenticated:
            return "unauthorized: initialize with valid MCP token first"
        return None

    def _handle_initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": "1.0",
            "serverInfo": {"name": "owlclaw-mcp-server", "version": "0.1.0"},
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"listChanged": True},
            },
        }

    def _handle_tools_list(self) -> dict[str, Any]:
        tools: list[dict[str, Any]] = []
        metadata_map = {
            item["name"]: item for item in self.registry.list_capabilities() if isinstance(item.get("name"), str)
        }
        for name, handler in self.registry.handlers.items():
            metadata = metadata_map.get(name, {})
            description = metadata.get("description") or self._handler_description(handler)
            tools.append(
                {
                    "name": name,
                    "description": description,
                    "inputSchema": self._build_input_schema(handler),
                    "governance": {
                        "constraints": metadata.get("constraints", {}),
                        "risk_level": metadata.get("risk_level", "low"),
                        "requires_confirmation": metadata.get("requires_confirmation", False),
                    },
                }
            )
        if self.signal_router is not None:
            tools.extend(self._signal_tool_defs())
        tools.sort(key=lambda item: item["name"])
        return {"tools": tools}

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params["name"]
        arguments = params.get("arguments", {})
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise McpRequestError(-32602, "tool name must be a non-empty string")
        if not isinstance(arguments, dict):
            raise McpRequestError(-32602, "arguments must be an object")
        if self.signal_router is not None and tool_name in _SIGNAL_TOOL_TO_TYPE:
            signal_result = await self._handle_signal_tool_call(tool_name, arguments)
            text = json.dumps(signal_result, ensure_ascii=False)
            return {"content": [{"type": "text", "text": text}]}
        if tool_name not in self.registry.handlers:
            raise McpRequestError(-32001, f"tool not found: {tool_name}")

        result = await self.registry.invoke_handler(tool_name, **arguments)
        text = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
        return {"content": [{"type": "text", "text": text}]}

    async def _handle_signal_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self.signal_router is not None
        agent_id = arguments.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise McpRequestError(-32602, "agent_id must be a non-empty string")
        tenant_id = arguments.get("tenant_id", "default")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise McpRequestError(-32602, "tenant_id must be a non-empty string")
        operator = arguments.get("operator", "mcp")
        if not isinstance(operator, str) or not operator.strip():
            raise McpRequestError(-32602, "operator must be a non-empty string")
        message = arguments.get("message", "")
        if not isinstance(message, str):
            raise McpRequestError(-32602, "message must be a string")
        focus = arguments.get("focus")
        if focus is not None and not isinstance(focus, str):
            raise McpRequestError(-32602, "focus must be a string when provided")
        ttl_seconds = arguments.get("ttl_seconds", 3600)
        if not isinstance(ttl_seconds, int):
            raise McpRequestError(-32602, "ttl_seconds must be an integer")

        signal_type = _SIGNAL_TOOL_TO_TYPE[tool_name]
        if signal_type == SignalType.INSTRUCT and not message.strip():
            raise McpRequestError(-32602, "message is required for owlclaw_instruct")

        signal = Signal(
            type=signal_type,
            source=SignalSource.MCP,
            agent_id=agent_id.strip(),
            tenant_id=tenant_id.strip(),
            operator=operator.strip(),
            message=message,
            focus=focus,
            ttl_seconds=ttl_seconds,
        )
        result = await self.signal_router.dispatch(signal)
        return {
            "status": result.status,
            "message": result.message,
            "run_id": result.run_id,
            "error_code": result.error_code,
        }

    def _signal_tool_defs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "owlclaw_pause",
                "description": "Pause one target agent.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "tenant_id": {"type": "string"},
                        "operator": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["agent_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "owlclaw_resume",
                "description": "Resume one paused agent.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "tenant_id": {"type": "string"},
                        "operator": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["agent_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "owlclaw_trigger",
                "description": "Force-trigger one agent run.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "tenant_id": {"type": "string"},
                        "operator": {"type": "string"},
                        "message": {"type": "string"},
                        "focus": {"type": "string"},
                    },
                    "required": ["agent_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "owlclaw_instruct",
                "description": "Queue one instruction for next run.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "tenant_id": {"type": "string"},
                        "operator": {"type": "string"},
                        "message": {"type": "string"},
                        "ttl_seconds": {"type": "integer"},
                    },
                    "required": ["agent_id", "message"],
                    "additionalProperties": False,
                },
            },
        ]

    def build_agent_card(
        self,
        *,
        url: str = "http://localhost:8080",
        name: str = "OwlClaw",
        description: str = "AI-powered business system intelligence",
        version: str = "0.1.0",
    ) -> dict[str, Any]:
        """Build a static A2A Agent Card payload."""
        governance_tools: list[str] = []
        task_tools: list[str] = []
        business_tools: list[str] = []

        for tool_name in sorted(self.registry.handlers):
            if tool_name.startswith("governance_"):
                governance_tools.append(tool_name)
            elif tool_name.startswith("task_"):
                task_tools.append(tool_name)
            else:
                business_tools.append(tool_name)
        if self.signal_router is not None:
            business_tools.extend(sorted(_SIGNAL_TOOL_TO_TYPE))

        return {
            "name": name,
            "description": description,
            "url": url,
            "version": version,
            "capabilities": {
                "governance": governance_tools,
                "tasks": task_tools,
                "business": business_tools,
            },
            "authentication": {
                "schemes": ["bearer"],
            },
            "protocols": {
                "mcp": {"transport": ["http", "stdio"]},
                "a2a": {"version": "0.1.0"},
            },
        }

    def _handle_resources_list(self) -> dict[str, Any]:
        self._refresh_resource_cache()
        resources = [
            {
                "uri": ref.uri,
                "name": ref.skill.name,
                "description": ref.skill.description,
                "mimeType": "text/markdown",
            }
            for ref in self._resource_cache.values()
        ]
        resources.sort(key=lambda item: item["uri"])
        return {"resources": resources}

    def _handle_resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params["uri"]
        if not isinstance(uri, str) or not uri.strip():
            raise McpRequestError(-32602, "uri must be a non-empty string")
        self._refresh_resource_cache()
        ref = self._resource_cache.get(uri)
        if ref is None:
            raise McpRequestError(-32002, f"resource not found: {uri}")

        content = ref.skill.file_path.read_text(encoding="utf-8")
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/markdown",
                    "text": content,
                }
            ]
        }

    def _refresh_resource_cache(self) -> None:
        if not self.skills_loader.skills:
            self.skills_loader.scan()
        cache: dict[str, ResourceRef] = {}
        for skill in self.skills_loader.list_skills():
            uri = self._resource_uri(skill)
            cache[uri] = ResourceRef(uri=uri, skill=skill)
        self._resource_cache = cache

    def _resource_uri(self, skill: Skill) -> str:
        skill_dir = skill.file_path.parent
        category = skill_dir.parent.name if skill_dir.parent.name else "default"
        name = skill_dir.name if skill_dir.name else skill.name
        return f"skill://{category}/{name}"

    @staticmethod
    def _handler_description(handler: Any) -> str:
        doc = inspect.getdoc(handler)
        if not doc:
            return "No description."
        return doc.splitlines()[0].strip()

    def _build_input_schema(self, handler: Any) -> dict[str, Any]:
        signature = inspect.signature(handler)
        properties: dict[str, Any] = {}
        required: list[str] = []

        for name, param in signature.parameters.items():
            if name in {"self", "session"}:
                continue
            if param.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                continue
            annotation = param.annotation if param.annotation is not inspect._empty else Any
            properties[name] = self._annotation_to_schema(annotation)
            if param.default is inspect._empty:
                required.append(name)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema

    def _annotation_to_schema(self, annotation: Any) -> dict[str, Any]:
        origin = get_origin(annotation)
        args = get_args(annotation)

        if annotation in {str, "str"}:
            return {"type": "string"}
        if annotation in {int, "int"}:
            return {"type": "integer"}
        if annotation in {float, "float"}:
            return {"type": "number"}
        if annotation in {bool, "bool"}:
            return {"type": "boolean"}
        if origin is list:
            item_type = args[0] if args else Any
            return {"type": "array", "items": self._annotation_to_schema(item_type)}
        if origin is dict:
            value_type = args[1] if len(args) > 1 else Any
            return {"type": "object", "additionalProperties": self._annotation_to_schema(value_type)}
        if origin is tuple:
            return {"type": "array"}
        if origin is None and annotation is Any:
            return {"type": "object"}
        if origin is None:
            return {"type": "object"}
        if origin in {Union, types.UnionType}:
            non_none = [item for item in args if item is not type(None)]
            if len(non_none) == 1 and len(args) == 2:
                schema = self._annotation_to_schema(non_none[0])
                schema["nullable"] = True
                return schema
            return {"oneOf": [self._annotation_to_schema(item) for item in non_none]}
        return {"type": "object"}

    @staticmethod
    def _success(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }


_SIGNAL_TOOL_TO_TYPE: dict[str, SignalType] = {
    "owlclaw_pause": SignalType.PAUSE,
    "owlclaw_resume": SignalType.RESUME,
    "owlclaw_trigger": SignalType.TRIGGER,
    "owlclaw_instruct": SignalType.INSTRUCT,
}
