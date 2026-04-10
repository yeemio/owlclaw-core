"""LLM integration — all LLM calls MUST go through this module.

This layer isolates litellm so that routing, tracing, and provider swap
can be centralized. Provides:

- acompletion(): minimal pass-through for litellm.acompletion (tests, simple use)
- LLMConfig, LLMClient: full config + routing + fallback (integrations-llm spec)
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from owlclaw.integrations.langfuse import TokenCalculator, TraceContext

logger = logging.getLogger(__name__)

_mock_config: dict[str, Any] | None = None
_DEFAULT_LLM_TIMEOUT_SECONDS = 30.0


class _MockFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _MockToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = call_id
        self.type = "function"
        self.function = _MockFunction(name=name, arguments=json.dumps(arguments, default=str))


class _MockMessage:
    def __init__(self, content: str | None, tool_calls: list[_MockToolCall]) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _MockChoice:
    def __init__(self, message: _MockMessage) -> None:
        self.message = message


class _MockUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _MockCompletionResponse:
    def __init__(
        self,
        *,
        content: str | None,
        function_calls: list[dict[str, Any]],
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        tool_calls = [
            _MockToolCall(
                call_id=str(fc.get("id", "")),
                name=str(fc.get("name", "")),
                arguments=fc.get("arguments", {}) if isinstance(fc.get("arguments"), dict) else {},
            )
            for fc in function_calls
            if isinstance(fc, dict)
        ]
        self.model = "mock"
        self.choices = [_MockChoice(_MockMessage(content=content, tool_calls=tool_calls))]
        self.usage = _MockUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


def _parse_mock_response(raw: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """Parse configured mock response into text content and function calls."""
    if isinstance(raw, str):
        return raw, []
    if not isinstance(raw, dict):
        return str(raw), []

    content = raw.get("content")
    if content is not None and not isinstance(content, str):
        content = str(content)

    function_calls_raw = raw.get("function_calls", [])
    function_calls: list[dict[str, Any]] = []
    if isinstance(function_calls_raw, list):
        for idx, fc in enumerate(function_calls_raw):
            if not isinstance(fc, dict):
                continue
            name = fc.get("name")
            if not isinstance(name, str) or not name:
                continue
            arguments = fc.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            call_id = fc.get("id")
            if not isinstance(call_id, str) or not call_id:
                call_id = f"mock_call_{idx + 1}"
            function_calls.append(
                {
                    "id": call_id,
                    "name": name,
                    "arguments": arguments,
                }
            )
    return content, function_calls


def _build_mock_completion_response(
    *,
    messages: Any,
    content: str | None,
    function_calls: list[dict[str, Any]],
) -> _MockCompletionResponse:
    prompt_chars = len(json.dumps(messages, default=str))
    completion_payload = content if content is not None else function_calls
    completion_chars = len(json.dumps(completion_payload, default=str))
    prompt_tokens = max(1, prompt_chars // 4)
    completion_tokens = max(0, completion_chars // 4)
    return _MockCompletionResponse(
        content=content,
        function_calls=function_calls,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def configure_mock(mock_responses: dict[str, Any] | None) -> None:
    """Configure module-level mock responses for ``acompletion``."""
    global _mock_config
    if mock_responses is None:
        _mock_config = None
        return
    _mock_config = dict(mock_responses)

# ---------------------------------------------------------------------------
# Minimal facade (architecture rule)
# ---------------------------------------------------------------------------


async def acompletion(**kwargs: Any) -> Any:
    """Async LLM completion. Delegates to litellm; all callers must use this.

    Args:
        **kwargs: Passed through to litellm.acompletion (model, messages,
            tools, tool_choice, etc.).

    Returns:
        litellm response object (e.g. choices[0].message with tool_calls).
    """
    timeout = kwargs.get("timeout")
    if timeout is None and kwargs.get("request_timeout") is None:
        kwargs["timeout"] = _DEFAULT_LLM_TIMEOUT_SECONDS

    if _mock_config is not None:
        task_type = kwargs.get("task_type")
        key = task_type if isinstance(task_type, str) and task_type.strip() else "default"
        raw_mock = _mock_config.get(key, _mock_config.get("default", ""))
        content, function_calls = _parse_mock_response(raw_mock)
        return _build_mock_completion_response(
            messages=kwargs.get("messages", []),
            content=content,
            function_calls=function_calls,
        )

    import litellm

    trace_ctx = TraceContext.get_current()
    trace = trace_ctx.metadata.get("langfuse_trace") if trace_ctx and trace_ctx.metadata else None
    model_name = str(kwargs.get("model", ""))
    started = time.perf_counter()
    try:
        response = await litellm.acompletion(**kwargs)
        if trace is not None and hasattr(trace, "generation"):
            prompt_tokens, completion_tokens, total_tokens = TokenCalculator.extract_tokens_from_response(response)
            cost = TokenCalculator.calculate_cost(model_name, prompt_tokens, completion_tokens)
            output_payload: Any = None
            try:
                first_choice = response.choices[0]
                message = first_choice.message
                output_payload = getattr(message, "content", None)
                if output_payload is None:
                    output_payload = getattr(message, "tool_calls", None)
            except Exception:
                output_payload = None
            with contextlib.suppress(Exception):
                trace.generation(
                    name="llm_completion",
                    model=model_name,
                    input=kwargs.get("messages"),
                    output=output_payload,
                    usage={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    },
                    metadata={
                        "cost_usd": cost,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                        "status": "success",
                    },
                )
        return response
    except Exception as exc:
        if trace is not None and hasattr(trace, "generation"):
            with contextlib.suppress(Exception):
                trace.generation(
                    name="llm_completion",
                    model=model_name,
                    input=kwargs.get("messages"),
                    output=None,
                    usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    metadata={
                        "cost_usd": 0.0,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                        "status": "error",
                        "error_type": exc.__class__.__name__,
                    },
                )
        raise


async def aembedding(**kwargs: Any) -> Any:
    """Async embedding facade. All embedding callers must use this."""
    timeout = kwargs.get("timeout")
    if timeout is None and kwargs.get("request_timeout") is None:
        kwargs["timeout"] = _DEFAULT_LLM_TIMEOUT_SECONDS
    import litellm

    return await litellm.aembedding(**kwargs)


@dataclass(frozen=True)
class CostInfo:
    """Normalized token and cost info for one model call."""

    prompt_tokens: int
    completion_tokens: int
    total_cost: float


def extract_cost_info(response: Any, *, model: str) -> CostInfo:
    """Extract usage tokens and estimated cost from a litellm-like response."""
    usage = (
        response.get("usage")
        if isinstance(response, dict)
        else getattr(response, "usage", None)
    )
    if usage is None:
        return CostInfo(prompt_tokens=0, completion_tokens=0, total_cost=0.0)

    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
    else:
        prompt_tokens = getattr(usage, "prompt_tokens", 0)
        completion_tokens = getattr(usage, "completion_tokens", 0)

    try:
        prompt = max(0, int(prompt_tokens))
    except (TypeError, ValueError):
        prompt = 0
    try:
        completion = max(0, int(completion_tokens))
    except (TypeError, ValueError):
        completion = 0

    if model.strip().lower() == "mock":
        return CostInfo(prompt_tokens=prompt, completion_tokens=completion, total_cost=0.0)

    total_cost = TokenCalculator.calculate_cost(model, prompt, completion)
    return CostInfo(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_cost=max(0.0, round(float(total_cost), 6)),
    )


# ---------------------------------------------------------------------------
# LLM error types (Task 6.1)
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base exception for LLM integration errors."""

    def __init__(self, message: str, *, model: str | None = None, cause: Exception | None = None):
        super().__init__(message)
        self.message = message
        self.model = model
        self.cause = cause


class AuthenticationError(LLMError):
    """Raised when API key is invalid or missing."""


class RateLimitError(LLMError):
    """Raised when provider rate limit is exceeded (retriable)."""


class ContextWindowExceededError(LLMError):
    """Raised when prompt exceeds model context window."""


class ServiceUnavailableError(LLMError):
    """Raised when LLM provider is unavailable (fallback may be attempted)."""


# ---------------------------------------------------------------------------
# LLMConfig (Task 1)
# ---------------------------------------------------------------------------


def _substitute_env(value: str) -> str:
    """Replace ${VAR} with environment variable values."""
    if not isinstance(value, str):
        return value
    pattern = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
    def repl(m: re.Match) -> str:
        name = m.group(1) or m.group(2) or ""
        return os.environ.get(name, "")
    return pattern.sub(repl, value)


def _substitute_env_any(value: Any) -> Any:
    """Recursively substitute ${VAR} in mappings, lists, and strings."""
    if isinstance(value, dict):
        return {k: _substitute_env_any(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env_any(v) for v in value]
    if isinstance(value, str):
        return _substitute_env(value)
    return value


class ModelConfig(BaseModel):
    """Single model configuration."""

    name: str
    provider: str
    api_key_env: str = "OPENAI_API_KEY"
    api_base: str | None = None  # e.g. https://api.siliconflow.cn/v1 for OpenAI-compatible
    temperature: float = 0.7
    max_tokens: int = 4096
    context_window: int = 128000
    supports_function_calling: bool = True
    cost_per_1k_prompt_tokens: float = 0.0
    cost_per_1k_completion_tokens: float = 0.0


class TaskTypeRouting(BaseModel):
    """task_type to model routing rule."""

    task_type: str
    model: str
    fallback_models: list[str] = Field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None


class LLMConfig(BaseModel):
    """LLM integration config (from owlclaw.yaml llm section)."""

    default_model: str = "gpt-4o-mini"
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    task_type_routing: list[TaskTypeRouting] = Field(default_factory=list)
    max_retries: int = 3
    retry_delay_seconds: float = 1.0
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    mock_mode: bool = False
    mock_responses: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> LLMConfig:
        """Load LLM config from owlclaw.yaml (llm section)."""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        llm_data = data.get("llm", {})
        llm_data = _substitute_env_any(llm_data)
        return cls.model_validate(llm_data)

    @classmethod
    def default_for_owlclaw(cls) -> LLMConfig:
        """Minimal config using default model (no yaml)."""
        return cls(
            default_model="gpt-4o-mini",
            models={
                "gpt-4o-mini": ModelConfig(
                    name="gpt-4o-mini",
                    provider="openai",
                    api_key_env="OPENAI_API_KEY",
                    cost_per_1k_prompt_tokens=0.00015,
                    cost_per_1k_completion_tokens=0.0006,
                ),
            },
        )


# ---------------------------------------------------------------------------
# LLMResponse, LLMClient (Task 2)
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """Unified LLM response shape."""

    content: str | None
    function_calls: list[dict[str, Any]]
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost: float

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient:
    """OwlClaw wrapper over litellm with config, routing, fallback."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._langfuse: Any = None
        self._langfuse_init_error: str | None = None
        if config.langfuse_enabled and config.langfuse_public_key and config.langfuse_secret_key:
            try:
                mod = importlib.import_module("langfuse")
                langfuse_cls = mod.Langfuse
                # Prefer base_url (new SDK); fallback to host (older SDK)
                try:
                    self._langfuse = langfuse_cls(
                        public_key=config.langfuse_public_key,
                        secret_key=config.langfuse_secret_key,
                        base_url=config.langfuse_host,
                    )
                except TypeError:
                    self._langfuse = langfuse_cls(
                        public_key=config.langfuse_public_key,
                        secret_key=config.langfuse_secret_key,
                        host=config.langfuse_host,
                    )
            except ImportError as e:
                logger.warning("langfuse not installed; tracing disabled")
                self._langfuse_init_error = f"langfuse not installed: {e}"
            except Exception as e:
                logger.warning("Langfuse init failed: %s", e)
                self._langfuse_init_error = str(e)
        try:
            import litellm
            litellm.drop_params = True
        except ImportError:
            pass

    def _route_model(self, task_type: str | None) -> tuple[str, ModelConfig, list[str]]:
        """Route task_type to model name, ModelConfig, and fallback list."""
        fallback: list[str] = []
        if task_type:
            for routing in self.config.task_type_routing:
                if routing.task_type == task_type:
                    model_name = routing.model
                    if model_name not in self.config.models:
                        raise ValueError(f"Unknown model '{model_name}' in task_type_routing")
                    return model_name, self.config.models[model_name], list(routing.fallback_models)
        model_name = self.config.default_model
        if model_name not in self.config.models:
            raise ValueError(f"Default model '{model_name}' not in config.models")
        return model_name, self.config.models[model_name], fallback

    def _get_task_routing(self, task_type: str | None) -> TaskTypeRouting | None:
        """Return routing entry for task_type, if configured."""
        if not task_type:
            return None
        for routing in self.config.task_type_routing:
            if routing.task_type == task_type:
                return routing
        return None

    def _wrap_litellm_error(self, e: Exception, model: str) -> LLMError:
        """Map litellm exception to OwlClaw LLM error and log details."""
        msg = str(e)
        err_name = type(e).__name__
        msg_lower = msg.lower()
        logger.warning(
            "LLM call failed model=%s error_type=%s message=%s",
            model,
            err_name,
            msg[:200] + ("..." if len(msg) > 200 else ""),
        )
        if "Authentication" in err_name or "authentication" in msg_lower or ("invalid" in msg_lower and "api" in msg_lower and "key" in msg_lower):
            return AuthenticationError(msg, model=model, cause=e)
        if self._is_rate_limit_error(err_name, msg_lower):
            return RateLimitError(msg, model=model, cause=e)
        if "ContextWindow" in err_name or ("context" in msg_lower and "window" in msg_lower):
            return ContextWindowExceededError(msg, model=model, cause=e)
        if "ServiceUnavailable" in err_name or "503" in msg or "unavailable" in msg_lower:
            return ServiceUnavailableError(msg, model=model, cause=e)
        return ServiceUnavailableError(
            f"LLM call failed: {msg}", model=model, cause=e
        )

    @staticmethod
    def _is_rate_limit_error(err_name: str, msg_lower: str) -> bool:
        """Detect provider rate-limit errors from type/message patterns."""
        err_lower = err_name.lower()
        return (
            "ratelimit" in err_lower
            or "rate_limit" in msg_lower
            or "ratelimit" in msg_lower
            or "too many requests" in msg_lower
        )

    async def _call_with_fallback(
        self,
        params: dict[str, Any],
        fallback_models: list[str],
    ) -> tuple[Any, str]:
        """Call litellm; on failure try fallback models.

        Returns:
            Tuple of (litellm response, model name actually used).
        """
        models_to_try = [params["model"]] + fallback_models
        last_error: Exception | None = None
        last_model = ""
        retries_per_model = max(1, int(self.config.max_retries))
        for model_idx, model in enumerate(models_to_try):
            last_model = model
            mc = self.config.models.get(model)
            call_params = {**params, "model": model}
            if mc:
                api_key = os.environ.get(mc.api_key_env, "").strip()
                if api_key:
                    call_params["api_key"] = api_key
                if mc.api_base:
                    call_params["api_base"] = mc.api_base
            for retry_idx in range(retries_per_model):
                try:
                    response = await acompletion(**call_params)
                    return response, model
                except Exception as e:
                    last_error = e
                    err_name = type(e).__name__
                    msg_lower = str(e).lower()
                    is_rate_limit = self._is_rate_limit_error(err_name, msg_lower)
                    if "Authentication" in err_name or "InvalidApiKey" in err_name:
                        raise self._wrap_litellm_error(e, model) from e
                    if is_rate_limit and retry_idx < retries_per_model - 1:
                        await asyncio.sleep(self.config.retry_delay_seconds)
                        continue
                    if model_idx < len(models_to_try) - 1:
                        logger.warning("Model %s failed, trying fallback: %s", model, e)
                    break
        if last_error:
            raise self._wrap_litellm_error(last_error, last_model) from last_error
        raise ServiceUnavailableError("All models failed", model=last_model)

    def _parse_response(self, response: Any, model: str) -> LLMResponse:
        """Parse litellm response into LLMResponse."""
        choice = response.choices[0]
        message = choice.message
        function_calls: list[dict[str, Any]] = []
        tool_calls = getattr(message, "tool_calls", None) or []
        for tc in tool_calls:
            name = getattr(getattr(tc, "function", None), "name", "unknown")
            args_raw = getattr(getattr(tc, "function", None), "arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {}
            function_calls.append({
                "id": getattr(tc, "id", ""),
                "name": name,
                "arguments": args,
            })
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        mc = self.config.models.get(model)
        cost = 0.0
        if mc:
            cost = (
                prompt_tokens / 1000 * mc.cost_per_1k_prompt_tokens
                + completion_tokens / 1000 * mc.cost_per_1k_completion_tokens
            )
        return LLMResponse(
            content=getattr(message, "content", None) or None,
            function_calls=function_calls,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=round(cost, 6),
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        task_type: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        """Complete LLM call with routing and fallback."""
        if stream:
            raise ValueError(
                "LLMClient.complete(stream=True) is not supported; "
                "this API returns a single LLMResponse."
            )
        if self.config.mock_mode and self.config.mock_responses:
            key = task_type or "default"
            raw_mock = self.config.mock_responses.get(key, self.config.mock_responses.get("default", ""))
            content, function_calls = _parse_mock_response(raw_mock)
            # Simulate token usage (~4 chars per token)
            prompt_chars = len(json.dumps(messages, default=str))
            completion_payload = content if content is not None else function_calls
            completion_chars = len(json.dumps(completion_payload, default=str))
            prompt_tokens = max(1, prompt_chars // 4)
            completion_tokens = max(0, completion_chars // 4)
            return LLMResponse(
                content=content,
                function_calls=function_calls,
                model="mock",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=0.0,
            )
        model_name, model_config, fallback = self._route_model(task_type)
        routing = self._get_task_routing(task_type)
        routed_temp = routing.temperature if routing is not None else None
        routed_max_tokens = routing.max_tokens if routing is not None else None
        temp = temperature if temperature is not None else (
            routed_temp if routed_temp is not None else model_config.temperature
        )
        max_tok = max_tokens if max_tokens is not None else (
            routed_max_tokens if routed_max_tokens is not None else model_config.max_tokens
        )
        estimator = TokenEstimator(model_name)
        estimated_prompt_tokens = estimator.estimate_messages_tokens(messages)
        if estimated_prompt_tokens > model_config.context_window:
            raise ContextWindowExceededError(
                (
                    f"Estimated prompt tokens ({estimated_prompt_tokens}) exceed context window "
                    f"({model_config.context_window}) for model '{model_name}'."
                ),
                model=model_name,
            )
        params: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_tok,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        trace: Any = None
        if self._langfuse:
            try:
                trace = self._langfuse.trace(
                    name="llm_call",
                    metadata={"task_type": task_type, "model": model_name},
                )
            except Exception as e:
                logger.warning("Langfuse trace create failed: %s", e)
        try:
            response, used_model = await self._call_with_fallback(params, fallback)
            llm_resp = self._parse_response(response, used_model)
            if trace:
                try:
                    trace.generation(
                        name="completion",
                        model=used_model,
                        input=messages,
                        output=llm_resp.content or llm_resp.function_calls,
                        usage={
                            "prompt_tokens": llm_resp.prompt_tokens,
                            "completion_tokens": llm_resp.completion_tokens,
                            "total_tokens": llm_resp.total_tokens,
                        },
                        metadata={
                            "cost": llm_resp.cost,
                            "fallback_used": used_model != model_name,
                            "actual_model": used_model,
                        },
                    )
                    if used_model != model_name:
                        trace.update(metadata={"fallback_used": True, "actual_model": used_model})
                except Exception as e:
                    logger.warning("Langfuse generation record failed: %s", e)
            return llm_resp
        except Exception as e:
            if trace:
                with contextlib.suppress(Exception):
                    trace.update(status="error", output=str(e))
            raise


# ---------------------------------------------------------------------------
# PromptBuilder (Task 3.1)
# ---------------------------------------------------------------------------


class PromptBuilder:
    """Build structured message lists for LLM calls."""

    @staticmethod
    def build_system_message(content: str) -> dict[str, Any]:
        """Build a system role message.

        Args:
            content: System prompt text.

        Returns:
            Message dict with role="system".
        """
        return {"role": "system", "content": content}

    @staticmethod
    def build_user_message(content: str) -> dict[str, Any]:
        """Build a user role message.

        Args:
            content: User message text.

        Returns:
            Message dict with role="user".
        """
        return {"role": "user", "content": content}

    @staticmethod
    def build_function_result_message(tool_call_id: str, name: str, result: Any) -> dict[str, Any]:
        """Build a tool result message after a function call.

        Args:
            tool_call_id: The ID from the original tool_call.
            name: The function name that was called.
            result: The function result (will be JSON-serialised).

        Returns:
            Message dict with role="tool".
        """
        content = result if isinstance(result, str) else json.dumps(result, default=str)
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }


# ---------------------------------------------------------------------------
# ToolsConverter (Task 3.2)
# ---------------------------------------------------------------------------


class ToolsConverter:
    """Convert OwlClaw capability definitions to OpenAI-compatible tool schemas."""

    @staticmethod
    def capabilities_to_tools(capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert a list of capability dicts to litellm/OpenAI tool format.

        Each capability dict should have:
          - name (str): tool name
          - description (str): what the tool does
          - parameters (dict, optional): JSON Schema for parameters

        Args:
            capabilities: List of capability descriptor dicts.

        Returns:
            List of tool dicts in OpenAI function-calling format.
        """
        tools: list[dict[str, Any]] = []
        for cap in capabilities:
            name = cap.get("name", "")
            description = cap.get("description", "")
            parameters = cap.get("parameters") or {"type": "object", "properties": {}}
            parameters = ToolsConverter._normalise_schema(parameters)
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            })
        return tools

    @staticmethod
    def _normalise_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """Ensure the schema has the required top-level fields.

        Adds "type": "object" and "properties": {} if missing so that the
        resulting schema is always a valid JSON Schema object descriptor.
        """
        result = dict(schema)
        result.setdefault("type", "object")
        result.setdefault("properties", {})
        return result


# ---------------------------------------------------------------------------
# TokenEstimator (Task 3.3)
# ---------------------------------------------------------------------------


class TokenEstimator:
    """Estimate token counts for messages and check context window limits."""

    # Fallback: ~4 characters per token (rough heuristic when tiktoken unavailable)
    _CHARS_PER_TOKEN = 4

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self._model = model
        self._encoding: Any = None
        self._tiktoken_available = False
        self._try_load_tiktoken()

    def _try_load_tiktoken(self) -> None:
        """Attempt to load tiktoken encoding for the configured model."""
        try:
            import tiktoken  # noqa: PLC0415

            try:
                self._encoding = tiktoken.encoding_for_model(self._model)
                self._tiktoken_available = True
                return
            except Exception:
                try:
                    self._encoding = tiktoken.get_encoding("cl100k_base")
                    self._tiktoken_available = True
                    return
                except Exception as e:
                    logger.debug(
                        "tiktoken encoding unavailable for model %s; "
                        "using character-based token estimate: %s",
                        self._model,
                        e,
                    )
        except ImportError:
            logger.debug("tiktoken not installed; using character-based token estimate")
        self._encoding = None
        self._tiktoken_available = False

    def estimate_tokens(self, text: str) -> int:
        """Estimate the number of tokens in a text string.

        Uses tiktoken when available, falls back to character heuristic.

        Args:
            text: Input text to estimate.

        Returns:
            Estimated token count (>= 1 for non-empty text).
        """
        if not text:
            return 0
        if self._tiktoken_available and self._encoding is not None:
            return len(self._encoding.encode(text))
        return max(1, len(text) // self._CHARS_PER_TOKEN)

    def estimate_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate total tokens for a list of messages.

        Adds a small per-message overhead (4 tokens) to approximate the
        OpenAI message-framing overhead.

        Args:
            messages: List of message dicts with "content" key.

        Returns:
            Estimated total token count.
        """
        total = 0
        for msg in messages:
            content = msg.get("content") or ""
            if not isinstance(content, str):
                content = json.dumps(content, default=str)
            total += self.estimate_tokens(content) + 4  # per-message overhead
        return total

    def check_context_window(self, messages: list[dict[str, Any]], context_window: int) -> bool:
        """Check whether the messages fit within the given context window.

        Args:
            messages: List of message dicts.
            context_window: Maximum token count allowed.

        Returns:
            True if messages fit; False if they exceed the context window.
        """
        estimated = self.estimate_messages_tokens(messages)
        if estimated > context_window:
            logger.warning(
                "Estimated %d tokens exceeds context window of %d for model %s",
                estimated,
                context_window,
                self._model,
            )
            return False
        return True
