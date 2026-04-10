"""Langfuse integration helpers (isolated observability layer)."""

from __future__ import annotations

import atexit
import logging
import os
import random
import re
import weakref
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)
_atexit_registered = False
_registered_langfuse_clients: weakref.WeakSet[LangfuseClient] = weakref.WeakSet()


def _flush_registered_clients() -> None:
    for client in list(_registered_langfuse_clients):
        client.flush()


def _register_client_flush(client: LangfuseClient) -> None:
    global _atexit_registered
    _registered_langfuse_clients.add(client)
    if _atexit_registered:
        return
    atexit.register(_flush_registered_clients)
    _atexit_registered = True


class SpanType(str, Enum):
    """Supported span types."""

    LLM = "llm"
    TOOL = "tool"
    GENERATION = "generation"
    SPAN = "span"


@dataclass
class LangfuseConfig:
    """Langfuse connection and behavior configuration."""

    enabled: bool = False
    public_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    host: str = "https://cloud.langfuse.com"
    sampling_rate: float = 1.0
    async_upload: bool = True
    batch_size: int = 10
    flush_interval_seconds: int = 5
    mask_inputs: bool = False
    mask_outputs: bool = False
    custom_mask_patterns: list[str] = field(default_factory=list)
    client: Any | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        """Serialize config with sensitive credentials redacted."""
        return {
            "enabled": self.enabled,
            "public_key": "***" if self.public_key else "",
            "secret_key": "***" if self.secret_key else "",
            "host": self.host,
            "sampling_rate": self.sampling_rate,
            "async_upload": self.async_upload,
            "batch_size": self.batch_size,
            "flush_interval_seconds": self.flush_interval_seconds,
            "mask_inputs": self.mask_inputs,
            "mask_outputs": self.mask_outputs,
            "custom_mask_patterns": list(self.custom_mask_patterns),
            "client": self.client,
        }


@dataclass
class TraceMetadata:
    """Metadata attached to a trace."""

    agent_id: str
    run_id: str
    trigger_type: str
    focus: str | None = None
    user_id: str | None = None
    session_id: str | None = None


@dataclass
class LLMSpanData:
    """LLM span payload."""

    model: str
    prompt: list[dict[str, Any]]
    response: str | dict[str, Any] | list[Any] | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: float
    status: str
    error_message: str | None = None


@dataclass
class ToolSpanData:
    """Tool span payload."""

    tool_name: str
    arguments: dict[str, Any]
    result: Any
    duration_ms: float
    status: str
    error_message: str | None = None


_trace_context: ContextVar[TraceContext | None] = ContextVar("trace_context", default=None)


@dataclass
class TraceContext:
    """Async context holder for trace/span propagation."""

    trace_id: str
    parent_span_id: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def get_current(cls) -> TraceContext | None:
        return _trace_context.get()

    @classmethod
    def set_current(cls, context: TraceContext | None) -> None:
        _trace_context.set(context)

    def with_parent_span(self, span_id: str) -> TraceContext:
        return TraceContext(
            trace_id=self.trace_id,
            parent_span_id=span_id,
            metadata=self.metadata,
        )


class TokenCalculator:
    """Token extraction and cost calculation."""

    MODEL_PRICING: dict[str, dict[str, float]] = {
        "gpt-4": {"prompt": 0.03 / 1000, "completion": 0.06 / 1000},
        "gpt-4-turbo": {"prompt": 0.01 / 1000, "completion": 0.03 / 1000},
        "gpt-4o-mini": {"prompt": 0.00015 / 1000, "completion": 0.0006 / 1000},
        "gpt-3.5-turbo": {"prompt": 0.0015 / 1000, "completion": 0.002 / 1000},
        "claude-3-opus": {"prompt": 0.015 / 1000, "completion": 0.075 / 1000},
        "claude-3-sonnet": {"prompt": 0.003 / 1000, "completion": 0.015 / 1000},
        "claude-3.5-sonnet": {"prompt": 0.003 / 1000, "completion": 0.015 / 1000},
        "claude-3.7-sonnet": {"prompt": 0.003 / 1000, "completion": 0.015 / 1000},
        "claude-3-haiku": {"prompt": 0.00025 / 1000, "completion": 0.00125 / 1000},
        "deepseek-chat": {"prompt": 0.00027 / 1000, "completion": 0.0011 / 1000},
        "deepseek-reasoner": {"prompt": 0.00055 / 1000, "completion": 0.00219 / 1000},
    }

    @classmethod
    def _normalize_model_name(cls, model: str) -> str:
        normalized = (model or "").strip().lower()
        if normalized.startswith("gpt-4o-mini"):
            return "gpt-4o-mini"
        if normalized.startswith("gpt-4-turbo"):
            return "gpt-4-turbo"
        if normalized.startswith("gpt-4"):
            return "gpt-4"
        if normalized.startswith("gpt-3.5"):
            return "gpt-3.5-turbo"
        if "claude-3-opus" in normalized:
            return "claude-3-opus"
        if "claude-3-sonnet" in normalized:
            return "claude-3-sonnet"
        if "claude-3.5-sonnet" in normalized or "claude-3-5-sonnet" in normalized:
            return "claude-3.5-sonnet"
        if "claude-3.7-sonnet" in normalized or "claude-3-7-sonnet" in normalized:
            return "claude-3.7-sonnet"
        if "claude-3-haiku" in normalized:
            return "claude-3-haiku"
        if normalized.startswith("deepseek/deepseek-chat") or normalized.startswith("deepseek-chat"):
            return "deepseek-chat"
        if normalized.startswith("deepseek/deepseek-reasoner") or normalized.startswith("deepseek-reasoner"):
            return "deepseek-reasoner"
        return normalized

    @classmethod
    def calculate_cost(cls, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        model_key = cls._normalize_model_name(model)
        pricing = cls.MODEL_PRICING.get(model_key, cls.MODEL_PRICING["gpt-3.5-turbo"])
        prompt_cost = max(0, int(prompt_tokens)) * pricing["prompt"]
        completion_cost = max(0, int(completion_tokens)) * pricing["completion"]
        return round(prompt_cost + completion_cost, 6)

    @classmethod
    def extract_tokens_from_response(cls, response: Any) -> tuple[int, int, int]:
        usage: Any = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
        if usage is None:
            return 0, 0, 0
        if isinstance(usage, dict):
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
            total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
            return prompt_tokens, completion_tokens, total_tokens
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)
        return prompt_tokens, completion_tokens, total_tokens


class PrivacyMasker:
    """Mask PII and secrets in observability payloads."""

    PII_PATTERNS: dict[str, re.Pattern[str]] = {
        "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "phone": re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    }
    SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
        "api_key": re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{10,}\b"),
        "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._-]+"),
        "password": re.compile(r'(?i)password["\']?\s*[:=]\s*["\']?([^"\'\s]+)'),
    }

    @classmethod
    def mask(cls, data: Any, config: LangfuseConfig) -> Any:
        if isinstance(data, str):
            return cls._mask_string(data, config.custom_mask_patterns)
        if isinstance(data, dict):
            return {k: cls.mask(v, config) for k, v in data.items()}
        if isinstance(data, list):
            return [cls.mask(item, config) for item in data]
        return data

    @classmethod
    def _mask_string(cls, text: str, custom_patterns: list[str] | None = None) -> str:
        masked = text
        for name, pattern in cls.PII_PATTERNS.items():
            masked = pattern.sub(f"[MASKED_{name.upper()}]", masked)
        for name, pattern in cls.SECRET_PATTERNS.items():
            masked = pattern.sub(f"[MASKED_{name.upper()}]", masked)
        for custom_pattern in custom_patterns or []:
            try:
                masked = re.compile(custom_pattern).sub("[MASKED_CUSTOM]", masked)
            except re.error:
                logger.warning("Invalid custom mask pattern: %s", custom_pattern)
        return masked


class LangfuseClient:
    """Thin wrapper around Langfuse SDK with graceful degradation."""

    def __init__(self, config: LangfuseConfig) -> None:
        self.config = config
        self._enabled = bool(config.enabled)
        self._client: Any | None = None
        self._init_error: str | None = None
        self._initialize_client()
        if self.enabled:
            _register_client_flush(self)

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    def _initialize_client(self) -> None:
        if self.config.client is not None:
            self._client = self.config.client
            self._enabled = True
            return
        if not self._enabled:
            return
        if not self.config.public_key or not self.config.secret_key:
            self._enabled = False
            self._init_error = "missing credentials"
            return
        try:
            module = __import__("langfuse")
            langfuse_cls = module.Langfuse
            try:
                self._client = langfuse_cls(
                    public_key=self.config.public_key,
                    secret_key=self.config.secret_key,
                    base_url=self.config.host,
                )
            except TypeError:
                self._client = langfuse_cls(
                    public_key=self.config.public_key,
                    secret_key=self.config.secret_key,
                    host=self.config.host,
                )
        except Exception as exc:
            self._enabled = False
            self._init_error = str(exc)
            logger.warning("Failed to initialize Langfuse client: %s", self._safe_error_message(exc))

    def _safe_error_message(self, error: Exception | str) -> str:
        text = str(error)
        for secret in (self.config.public_key, self.config.secret_key):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    def _should_sample(self) -> bool:
        rate = self.config.sampling_rate
        try:
            rate_float = float(rate)
        except (TypeError, ValueError):
            rate_float = 1.0
        rate_float = min(1.0, max(0.0, rate_float))
        return random.random() < rate_float

    def create_trace(self, name: str, metadata: TraceMetadata, tags: list[str] | None = None) -> str | None:
        if not self.enabled or not self._should_sample():
            return None
        client = self._client
        if client is None:
            return None
        try:
            trace = client.trace(
                name=name,
                metadata={
                    "agent_id": metadata.agent_id,
                    "run_id": metadata.run_id,
                    "trigger_type": metadata.trigger_type,
                    "focus": metadata.focus,
                    "user_id": metadata.user_id,
                    "session_id": metadata.session_id,
                },
                tags=tags or [],
            )
            return str(getattr(trace, "id", None) or "")
        except Exception as exc:
            logger.warning("Failed to create trace: %s", self._safe_error_message(exc))
            return None

    def end_trace(self, trace_id: str, output: Any | None = None, metadata: dict[str, Any] | None = None) -> None:
        if not self.enabled or not trace_id:
            return
        client = self._client
        if client is None:
            return
        try:
            client.trace(id=trace_id, output=output, metadata=metadata or {})
        except Exception as exc:
            logger.warning("Failed to end trace: %s", self._safe_error_message(exc))

    def create_llm_span(
        self,
        trace_id: str,
        span_name: str,
        data: LLMSpanData,
        parent_span_id: str | None = None,
    ) -> str | None:
        if not self.enabled or not trace_id:
            return None
        client = self._client
        if client is None:
            return None
        try:
            span_input = data.prompt
            span_output = data.response
            if self.config.mask_inputs:
                span_input = PrivacyMasker.mask(span_input, self.config)
            if self.config.mask_outputs:
                span_output = PrivacyMasker.mask(span_output, self.config)
            generation = client.generation(
                trace_id=trace_id,
                name=span_name,
                model=data.model,
                input=span_input,
                output=span_output,
                usage={
                    "prompt_tokens": data.prompt_tokens,
                    "completion_tokens": data.completion_tokens,
                    "total_tokens": data.total_tokens,
                },
                metadata={
                    "cost_usd": data.cost_usd,
                    "latency_ms": data.latency_ms,
                    "status": data.status,
                    "error_message": data.error_message,
                },
                parent_observation_id=parent_span_id,
            )
            return str(getattr(generation, "id", None) or "")
        except Exception as exc:
            logger.warning("Failed to create LLM span: %s", self._safe_error_message(exc))
            return None

    def create_tool_span(
        self,
        trace_id: str,
        span_name: str,
        data: ToolSpanData,
        parent_span_id: str | None = None,
    ) -> str | None:
        if not self.enabled or not trace_id:
            return None
        client = self._client
        if client is None:
            return None
        try:
            span_input: Any = {"tool_name": data.tool_name, "arguments": data.arguments}
            span_output: Any = data.result
            if self.config.mask_inputs:
                span_input = PrivacyMasker.mask(span_input, self.config)
            if self.config.mask_outputs:
                span_output = PrivacyMasker.mask(span_output, self.config)
            span = client.span(
                trace_id=trace_id,
                name=span_name,
                input=span_input,
                output=span_output,
                metadata={
                    "duration_ms": data.duration_ms,
                    "status": data.status,
                    "error_message": data.error_message,
                },
                parent_observation_id=parent_span_id,
            )
            return str(getattr(span, "id", None) or "")
        except Exception as exc:
            logger.warning("Failed to create tool span: %s", self._safe_error_message(exc))
            return None

    def flush(self) -> None:
        if not self.enabled:
            return
        flush_fn = getattr(self._client, "flush", None)
        if not callable(flush_fn):
            return
        try:
            flush_fn()
        except Exception as exc:
            logger.warning("Failed to flush Langfuse client: %s", self._safe_error_message(exc))


def _replace_env_vars(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _replace_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_env_vars(v) for v in value]
    if isinstance(value, str):
        pattern = re.compile(r"\$\{([^}]+)\}")
        out = value
        for var_name in pattern.findall(value):
            out = out.replace(f"${{{var_name}}}", os.environ.get(var_name, ""))
        return out
    return value


def load_langfuse_config(config_path: str | Path) -> LangfuseConfig:
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    langfuse_raw = raw.get("langfuse", raw)
    replaced = _replace_env_vars(langfuse_raw)
    return LangfuseConfig(
        enabled=bool(replaced.get("enabled", False)),
        public_key=str(replaced.get("public_key", "")),
        secret_key=str(replaced.get("secret_key", "")),
        host=str(replaced.get("host", "https://cloud.langfuse.com")),
        sampling_rate=float(replaced.get("sampling_rate", 1.0)),
        async_upload=bool(replaced.get("async_upload", True)),
        batch_size=int(replaced.get("batch_size", 10)),
        flush_interval_seconds=int(replaced.get("flush_interval_seconds", 5)),
        mask_inputs=bool(replaced.get("mask_inputs", False)),
        mask_outputs=bool(replaced.get("mask_outputs", False)),
        custom_mask_patterns=list(replaced.get("custom_mask_patterns", [])),
    )


def validate_config(config: LangfuseConfig) -> list[str]:
    errors: list[str] = []
    if config.enabled and config.client is None:
        if not config.public_key:
            errors.append("public_key is required when Langfuse is enabled")
        if not config.secret_key:
            errors.append("secret_key is required when Langfuse is enabled")
    if not (0.0 <= config.sampling_rate <= 1.0):
        errors.append("sampling_rate must be between 0 and 1")
    if config.batch_size <= 0:
        errors.append("batch_size must be positive")
    if config.flush_interval_seconds <= 0:
        errors.append("flush_interval_seconds must be positive")
    for pattern in config.custom_mask_patterns:
        try:
            re.compile(pattern)
        except re.error:
            errors.append(f"invalid custom mask pattern: {pattern}")
    return errors
